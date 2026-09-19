/*
 * camera_bridge: stream frames from a QNX Sensor Framework camera to stdout for run.py.
 *
 * Based on QNX's camera_example1_callback (Apache-2.0, (c) 2024 BlackBerry Limited):
 * https://gitlab.com/qnx/projects/camera-projects/applications/camera_example1_callback
 *
 * Wire format, per frame (little-endian):
 *   char magic[4] = "OVF1"; uint32 width; uint32 height; uint32 format; uint64 timestamp_us;
 *   then the pixel data with row stride removed:
 *     format 1 RGBX (CAMERA_FRAMETYPE_RGB8888)  width*height*4 bytes
 *     format 2 BGRX (CAMERA_FRAMETYPE_BGR8888)  width*height*4 bytes
 *     format 3 NV12                              width*height*3/2 bytes (Y plane then UV plane)
 *
 * Usage:  camera_bridge -u <unit> [-w width -h height]   | python3 run.py --source pipe:-
 *    or:  python3 run.py --source "pipe:camera_bridge -u 1"
 * Needs the sensor service running (see README.md).
 */
#include <camera/camera_api.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static volatile sig_atomic_t g_stop = 0;
static uint8_t* g_scratch = NULL;
static size_t g_scratch_size = 0;

static void on_signal(int sig) { (void)sig; g_stop = 1; }

static int write_all(const void* p, size_t n)
{
    const uint8_t* b = (const uint8_t*)p;
    while (n > 0) {
        ssize_t w = write(STDOUT_FILENO, b, n);
        if (w < 0) {
            if (errno == EINTR) continue;
            return -1;  /* reader went away */
        }
        b += w;
        n -= (size_t)w;
    }
    return 0;
}

/* Copy `rows` rows of `row_bytes` from a strided buffer into g_scratch at `offset`. */
static void pack_rows(const uint8_t* src, uint64_t stride, uint32_t row_bytes, uint32_t rows, size_t offset)
{
    for (uint32_t y = 0; y < rows; y++) {
        memcpy(g_scratch + offset + (size_t)y * row_bytes, src + (size_t)y * stride, row_bytes);
    }
}

static int ensure_scratch(size_t n)
{
    if (n <= g_scratch_size) return 0;
    uint8_t* p = realloc(g_scratch, n);
    if (p == NULL) return -1;
    g_scratch = p;
    g_scratch_size = n;
    return 0;
}

static void on_frame(camera_handle_t handle, camera_buffer_t* buf, void* arg)
{
    (void)handle;
    (void)arg;
    if (g_stop) return;
    uint32_t w = 0, h = 0, fmt = 0;
    size_t n = 0;
    switch (buf->frametype) {
    case CAMERA_FRAMETYPE_RGB8888:
    case CAMERA_FRAMETYPE_BGR8888: {
        int rgb = buf->frametype == CAMERA_FRAMETYPE_RGB8888;
        w = rgb ? buf->framedesc.rgb8888.width : buf->framedesc.bgr8888.width;
        h = rgb ? buf->framedesc.rgb8888.height : buf->framedesc.bgr8888.height;
        uint64_t stride = rgb ? buf->framedesc.rgb8888.stride : buf->framedesc.bgr8888.stride;
        fmt = rgb ? 1u : 2u;
        n = (size_t)w * h * 4u;
        if (ensure_scratch(n) != 0) return;
        pack_rows(buf->framebuf, stride, w * 4u, h, 0);
        break;
    }
    case CAMERA_FRAMETYPE_NV12: {
        w = buf->framedesc.nv12.width;
        h = buf->framedesc.nv12.height;
        fmt = 3u;
        n = (size_t)w * h * 3u / 2u;
        if (ensure_scratch(n) != 0) return;
        pack_rows(buf->framebuf, buf->framedesc.nv12.stride, w, h, 0);
        pack_rows(buf->framebuf + buf->framedesc.nv12.uv_offset, (uint64_t)buf->framedesc.nv12.uv_stride,
                  w, h / 2u, (size_t)w * h);
        break;
    }
    default:
        fprintf(stderr, "camera_bridge: unsupported frametype %d\n", (int)buf->frametype);
        g_stop = 1;
        return;
    }
    struct __attribute__((packed)) {
        char magic[4];
        uint32_t w, h, fmt;
        uint64_t ts;
    } hdr = {{'O', 'V', 'F', '1'}, w, h, fmt, (uint64_t)buf->frametimestamp};
    if (write_all(&hdr, sizeof hdr) != 0 || write_all(g_scratch, n) != 0) {
        g_stop = 1;
    }
}

int main(int argc, char** argv)
{
    long unit = 0, width = 0, height = 0;
    int opt;
    while ((opt = getopt(argc, argv, "u:w:h:")) != -1) {
        switch (opt) {
        case 'u': unit = strtol(optarg, NULL, 10); break;
        case 'w': width = strtol(optarg, NULL, 10); break;
        case 'h': height = strtol(optarg, NULL, 10); break;
        default:
            fprintf(stderr, "usage: %s -u <camera_unit> [-w width -h height]\n", argv[0]);
            return 1;
        }
    }
    if (unit <= (long)CAMERA_UNIT_NONE || unit >= (long)CAMERA_UNIT_NUM_UNITS) {
        fprintf(stderr, "camera_bridge: give a camera unit with -u (1 = first camera)\n");
        return 1;
    }
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, on_signal);

    camera_handle_t handle = CAMERA_HANDLE_INVALID;
    camera_error_t err = camera_open((camera_unit_t)unit, CAMERA_MODE_RO | CAMERA_MODE_PWRITE, &handle);
    if (err != CAMERA_EOK || handle == CAMERA_HANDLE_INVALID) {
        fprintf(stderr, "camera_bridge: camera_open(unit %ld) failed: %d (is the sensor service running?)\n", unit, err);
        return 1;
    }
    /* Prefer packed colour so Python does no YUV work; fall back to NV12, then whatever the camera gives. */
    const camera_frametype_t prefs[] = {CAMERA_FRAMETYPE_BGR8888, CAMERA_FRAMETYPE_RGB8888, CAMERA_FRAMETYPE_NV12};
    for (size_t i = 0; i < sizeof prefs / sizeof prefs[0]; i++) {
        if (camera_set_vf_property(handle, CAMERA_IMGPROP_FORMAT, prefs[i]) == CAMERA_EOK) break;
    }
    if (width > 0 && height > 0) {
        err = camera_set_vf_property(handle, CAMERA_IMGPROP_WIDTH, (int)width, CAMERA_IMGPROP_HEIGHT, (int)height);
        if (err != CAMERA_EOK) {
            fprintf(stderr, "camera_bridge: %ldx%ld not accepted (%d); using the camera default\n", width, height, err);
        }
    }
    err = camera_start_viewfinder(handle, on_frame, NULL, NULL);
    if (err != CAMERA_EOK) {
        fprintf(stderr, "camera_bridge: camera_start_viewfinder failed: %d\n", err);
        camera_close(handle);
        return 1;
    }
    while (!g_stop) {
        usleep(50000);
    }
    camera_stop_viewfinder(handle);
    camera_close(handle);
    free(g_scratch);
    return 0;
}

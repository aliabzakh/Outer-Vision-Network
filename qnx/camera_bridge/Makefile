# Build on the host with QNX SDP 8.0 sourced (source ~/qnx800/qnxsdp-env.sh), then copy to the Pi.
# Needs package com.qnx.qnx800.target.sf.base.group (camera API headers + libcamapi).
CC = qcc
CFLAGS = -Vgcc_ntoaarch64le -O2 -Wall -Wextra
LDLIBS = -lcamapi

camera_bridge: camera_bridge.c
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

clean:
	rm -f camera_bridge

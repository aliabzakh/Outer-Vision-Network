// Cover art for a song: the table as the player saw it (bearings from their head), the played path on top.
const FILL = { red: '#e5484d', orange: '#f76b15', yellow: '#ffc53d', green: '#30a46c', cyan: '#00a2c7',
  blue: '#3e63dd', purple: '#8e4ec6', pink: '#d6409f' };

export function layoutSvg(song, size = 600) {
  const [hf, vf] = song.quant.fov_deg;
  const px = (r) => [size / 2 + (r.azimuth_deg / hf) * size * 0.9, size / 2 - (r.elevation_deg / vf) * size * 0.9];
  const mark = (o, rad) => {
    const [x, y] = px(o);
    const c = FILL[o.color] || '#888';
    if (o.shape === 'square') return `<rect x="${x - rad}" y="${y - rad}" width="${2 * rad}" height="${2 * rad}" rx="4" fill="${c}"/>`;
    if (o.shape === 'cylinder') return `<rect x="${x - rad * 0.6}" y="${y - rad * 1.2}" width="${1.2 * rad}" height="${2.4 * rad}" rx="${rad * 0.6}" fill="${c}"/>`;
    return `<circle cx="${x}" cy="${y}" r="${rad}" fill="${c}"/>`;
  };
  const path = song.notes.map((n) => px(n).map((v) => v.toFixed(1)).join(',')).join(' ');
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}">
<rect width="${size}" height="${size}" fill="#101014"/>
<g stroke="#ffffff14">${[1, 2, 3].map((i) => `<circle cx="${size / 2}" cy="${size / 2}" r="${i * size / 7}" fill="none"/>`).join('')}</g>
${song.layout.map((o) => mark(o, 26)).join('\n')}
<polyline points="${path}" fill="none" stroke="#ffffffcc" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>
<text x="20" y="${size - 20}" fill="#ffffff88" font-family="monospace" font-size="16">${song.fingerprint.slice(0, 16)}</text>
</svg>`;
}

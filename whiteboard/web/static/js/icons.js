// 线条风格图标（24×24，描边用 currentColor），界面里只用图标不用文字。

const path = (d) => `<path d="${d}"/>`;

export const ICONS = {
  pen: path("M4 20l4.4-1.1L19 8.3a2.1 2.1 0 0 0-3-3L5.4 15.9 4 20z"),
  marker: path("M4.5 19.5h4.2l10.1-10.1a2.4 2.4 0 0 0 0-3.4l-1.8-1.8L4.5 16.7v2.8z") +
    path("M13.2 6.6l4.2 4.2"),
  highlighter: path("M9 15.2l6.4-6.4 3.4 3.4-6.4 6.4H9v-3.4z") + path("M6 19.6h3") +
    `<path d="M3.5 21.8h17" stroke-width="3.4" opacity=".45"/>`,
  eraser: path("M5.2 15.2l8.4-8.4a2 2 0 0 1 2.8 0l3.1 3.1a2 2 0 0 1 0 2.8L13.4 19H8.6l-3.4-3.8z") +
    path("M9.6 10.8l6 6") + path("M9 19h11"),
  undo: path("M5 9.5h9.5a4.75 4.75 0 1 1 0 9.5H9") + path("M5 9.5L9 5.5") + path("M5 9.5l4 4"),
  trash: path("M4.5 7h15") + path("M9.5 7V4.8h5V7") + path("M6.8 7l1 12.2h8.4L17 7") +
    path("M10.3 10.5v5.5") + path("M13.7 10.5v5.5"),
  boards: path("M4 4.5h6.2v6.2H4zM13.8 4.5H20v6.2h-6.2zM4 13.3h6.2v6.2H4zM13.8 13.3H20v6.2h-6.2z"),
  add: path("M12 5v14") + path("M5 12h14"),
  settings: path("M4 8h5") + path("M13 8h7") + path("M4 16h9") + path("M17 16h3") +
    `<circle cx="11" cy="8" r="2.4"/><circle cx="15" cy="16" r="2.4"/>`,
  image: path("M4.5 5.5h15v13h-15z") + path("M4.5 15l4.2-4 4 3.6 3-2.6 3.8 3.4") +
    `<circle cx="9" cy="9.3" r="1.5"/>`,
  tablet: path("M6.5 3.5h11a1.5 1.5 0 0 1 1.5 1.5v14a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19V5a1.5 1.5 0 0 1 1.5-1.5z") +
    path("M10.5 17.6h3"),
  zoomIn: `<circle cx="11" cy="11" r="6.2"/>` + path("M15.6 15.6L20 20") + path("M11 8.6v4.8") + path("M8.6 11h4.8"),
  zoomOut: `<circle cx="11" cy="11" r="6.2"/>` + path("M15.6 15.6L20 20") + path("M8.6 11h4.8"),
  fit: path("M4 9V4.8h4.4") + path("M15.6 4.8H20V9") + path("M20 15v4.2h-4.4") + path("M8.4 19.2H4V15"),
  close: path("M6 6l12 12") + path("M18 6L6 18"),
  check: path("M5 12.5l4.5 4.5L19 7.5"),
  folder: path("M4 6.8h5.4l1.8 2.2H20v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18V6.8z"),
  folderOpen: path("M4 18.4V6.8h5.4l1.8 2.2h6.6v2.2") +
    path("M4.2 18.4l2.6-6.4h14l-2.6 6.4H4.2z"),
  download: path("M12 4.5v10.5") + path("M8 11l4 4 4-4") + path("M5 19.5h14"),
  palette: `<circle cx="12" cy="12" r="8"/>`,
  note:
    path("M6.6 3.8h10.8v12.6H6.6z") +
    path("M9.2 7.4h5.6M9.2 10.3h5.6M9.2 13.2h3.4") +
    path("M12 17.6v3.6") +
    path("M9.9 19.2 12 21.4l2.1-2.2"),
  board:
    path("M7.4 7.4h9.2v9.2H7.4z") +
    path("M12 5.4V2.3M10.7 3.5 12 2.2l1.3 1.3") +
    path("M12 18.6v3.1M10.7 20.5 12 21.8l1.3-1.3") +
    path("M5.4 12H2.3M3.5 10.7 2.2 12l1.3 1.3") +
    path("M18.6 12h3.1M20.5 10.7 21.8 12l-1.3 1.3"),
  doc:
    path("M7 3.6h6.6L18 8v12.4H7z") +
    path("M13.4 3.8V8H17.8") +
    path("M9.6 12.4h5.4M9.6 15.4h5.4M9.6 18.2h3"),
  // 圆角条代表工具栏本身，箭头指向它要去的那一边
  dockTop: `<rect x="3.6" y="3" width="16.8" height="4.6" rx="2.3"/>` +
    path("M12 20.6v-9.2") + path("M8.8 14.6 12 11.4l3.2 3.2"),
  dockBottom: `<rect x="3.6" y="16.4" width="16.8" height="4.6" rx="2.3"/>` +
    path("M12 3.4v9.2") + path("M8.8 9.4 12 12.6l3.2-3.2"),
  info: `<circle cx="12" cy="12" r="8.6"/>` + path("M12 11.2v5") + path("M12 7.9h.01"),
  history: path("M4.2 12a7.8 7.8 0 1 0 2.4-5.6") + path("M4 5.8v4h4") + path("M12 8.2V12l2.8 1.8"),
  refresh: path("M19.4 12a7.4 7.4 0 1 1-2.2-5.3") + path("M19.8 4.7v4.3h-4.3"),
  hand: path("M9.6 12.2V6.1a1.6 1.6 0 1 1 3.2 0v4.4") +
    path("M12.8 10.5V9.2a1.6 1.6 0 1 1 3.2 0v1.7") +
    path("M16 11.3a1.6 1.6 0 1 1 3.2 0v3.4a5.1 5.1 0 0 1-5.1 5.1h-1.5a4.2 4.2 0 0 1-3-1.3l-3.7-3.8a1.7 1.7 0 0 1 2.4-2.4l1.3 1.3"),
  link: path("M10.5 13.5a3.5 3.5 0 0 0 5 0l2.5-2.5a3.5 3.5 0 0 0-5-5l-1 1") +
    path("M13.5 10.5a3.5 3.5 0 0 0-5 0L6 13a3.5 3.5 0 0 0 5 5l1-1"),
};

export function icon(name, size = 24) {
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor"
    stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
}

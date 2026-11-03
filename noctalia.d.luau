-- Noctalia v5 (plugin_api = 32) Luau Type Definitions for TachoPulse

export type UiNode = { [string]: any }

export type TooltipRow = {
  key: string,
  value: string,
}

export type RunResult = {
  exitCode: number,
  stdout: string?,
  stderr: string?,
}

declare noctalia: {
  getConfig: (key: string) -> any,
  getSetting: (key: string) -> any,
  setUpdateInterval: (ms: number) -> (),
  pluginDir: () -> string,
  pluginDataDir: () -> string?,
  getenv: (name: string) -> string?,
  nowMs: () -> number,
  isDarkMode: () -> boolean,
  tr: (key: string, params: { [string]: any }?) -> string,
  togglePanel: (panelId: string, context: string?) -> (),
  openSettings: () -> (),
  fileExists: (path: string) -> boolean,
  readFile: (path: string) -> string?,
  readFileAsync: (path: string, callback: (content: string?, err: string?) -> ()) -> (),
  writeFile: (path: string, content: string) -> boolean,
  removeFile: (path: string) -> boolean,
  commandExists: (cmd: string) -> boolean,
  runAsync: (cmd: string, callback: (result: RunResult) -> (), timeoutMs: number?) -> boolean,
  runStream: (cmd: string, onLine: (line: string) -> ()) -> boolean,
  systemStats: () -> { [string]: any }?,
  diskStats: (path: string) -> { [string]: any }?,
  string: {
    trim: (s: string) -> string,
  },
  json: {
    encode: (val: any) -> string?,
    decode: (raw: string) -> any,
  },
  state: {
    get: (key: string) -> any,
    set: (key: string, val: any) -> (),
    watch: (key: string, callback: (val: any) -> ()) -> (),
  },
}

declare ui: {
  row: (props: { [string]: any }, children: { UiNode }?) -> UiNode,
  column: (props: { [string]: any }, children: { UiNode }?) -> UiNode,
  box: (props: { [string]: any }, children: { UiNode }?) -> UiNode,
  scroll: (props: { [string]: any }, children: { UiNode }?) -> UiNode,
  label: (props: { [string]: any }) -> UiNode,
  glyph: (props: { [string]: any }) -> UiNode,
  image: (props: { [string]: any }) -> UiNode,
  button: (props: { [string]: any }) -> UiNode,
  progress: (props: { [string]: any }) -> UiNode,
  spacer: (props: { [string]: any }?) -> UiNode,
  separator: (props: { [string]: any }?) -> UiNode,
  input: (props: { [string]: any }) -> UiNode,
  select: (props: { [string]: any }) -> UiNode,
  toggle: (props: { [string]: any }) -> UiNode,
  slider: (props: { [string]: any }) -> UiNode,
  graph: (props: { [string]: any }) -> UiNode,
  markdown: (props: { [string]: any }) -> UiNode,
  dragSource: (props: { [string]: any }, children: { UiNode }?) -> UiNode,
  dropZone: (props: { [string]: any }) -> UiNode,
}

declare barWidget: {
  render: (node: UiNode) -> (),
  setGlyph: (name: string) -> (),
  setText: (text: string) -> (),
  setTooltip: (rows: { TooltipRow }) -> (),
  clearTooltip: () -> (),
  isVertical: () -> boolean,
}

declare desktopWidget: {
  render: (node: UiNode) -> (),
}

declare panel: {
  render: (node: UiNode) -> (),
  close: () -> (),
  setWantsSecondTicks: (enabled: boolean) -> (),
}

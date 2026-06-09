Bundled browser runtime files used by `embykeeperapi/static/index.html`.

Sources:
- `vue.global.prod.js`: https://cdn.jsdelivr.net/npm/vue@3/dist/vue.global.prod.js
- `naive-ui.prod.js`: https://cdn.jsdelivr.net/npm/naive-ui@2/dist/index.prod.js
- `vue-router.global.prod.js`: https://cdn.jsdelivr.net/npm/vue-router@4/dist/vue-router.global.prod.js

These packages are MIT licensed. They are kept in the repository so source,
HuggingFace, and development deployments do not depend on browser access to a
public CDN at runtime. If a local vendor file is missing, the WebUI falls back
to `https://cdn.jsdelivr.net`.

# Frontend build

This folder only holds a **built** React app; the frontend source lives elsewhere.

Copy the contents of your build output here so that `index.html` sits directly in
`frontend/dist/`:

```bash
# Vite: output is dist/
cp -r /path/to/react-app/dist/. frontend/dist/

# Create React App: output is build/
cp -r /path/to/react-app/build/. frontend/dist/
```

The backend serves this folder at `/` — the same idea as `express.static(buildPath)`
in a Node app. Under docker compose the folder is mounted, so a new build is live
as soon as it is copied: no rebuild, no restart.

## Build the app for same-origin serving

The frontend and the API share one origin, so point API calls at a relative path:

```bash
VITE_API_URL=""                # requests go to /api/v1/...  (Vite)
REACT_APP_API_URL=""           # same, for Create React App
```

Do not bake in `http://localhost:6050`: a relative path keeps working wherever
the app is deployed, whatever the host and port. Because the page and the API
share an origin, the session cookie is sent automatically;
`credentials: "include"` is harmless but no longer required.

For `npm run dev`, keep the same empty value and proxy API calls to the backend
in `vite.config.ts`, so development is same-origin too:

```ts
server: { proxy: { "/api": "http://localhost:6050", "/docs": "http://localhost:6050" } }
```

Client-side routes work on refresh: any path that is not a real file is served
`index.html`.

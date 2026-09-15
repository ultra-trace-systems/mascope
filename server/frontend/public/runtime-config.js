// Placeholder for the runtime configuration the container writes at start.
//
// In a deployed image `docker-entrypoint.sh` overwrites this file with
//   window.__MASCOPE_RUNTIME__ = { ...the backend's serialized runtime... };
// so the app reads the SAME config the backend is running on, rather than
// whatever was baked into the bundle when the image was built (see
// src/lib/runtime.js). Shipping an empty file rather than nothing means the
// <script> tag in index.html never 404s: in `vite dev`, in a test, and in a
// container started without MASCOPE_RUNTIME, the baked value simply stands.

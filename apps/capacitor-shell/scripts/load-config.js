#!/usr/bin/env node
/**
 * Reads apps/capacitor-shell/.env and writes capacitor.config.json for `npx cap sync`.
 */
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const envPath = path.join(root, ".env");
const examplePath = path.join(root, ".env.example");

function parseEnv(text) {
  const out = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let val = trimmed.slice(eq + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    out[key] = val;
  }
  return out;
}

function loadEnv() {
  const file = fs.existsSync(envPath) ? envPath : examplePath;
  if (!fs.existsSync(file)) {
    console.warn("No .env or .env.example — using defaults");
    return {};
  }
  return parseEnv(fs.readFileSync(file, "utf8"));
}

function normalizeServerUrl(raw, defaultPort) {
  let url = (raw || `http://127.0.0.1:${defaultPort}`).trim().replace(/\/$/, "");
  if (!/^https?:\/\//i.test(url)) {
    url = `http://${url}`;
    console.warn(`CAP_SERVER_URL missing http:// — normalized to ${url}`);
  }
  try {
    const parsed = new URL(url);
    if (!parsed.port) {
      parsed.port = String(defaultPort);
      url = parsed.toString().replace(/\/$/, "");
      console.warn(`CAP_SERVER_URL had no port — using :${defaultPort} → ${url}`);
    }
  } catch (e) {
    console.warn(`Could not parse CAP_SERVER_URL: ${e.message}`);
  }
  return url;
}

function assertServerPort(url, requiredPort) {
  if (!requiredPort) return;
  try {
    const parsed = new URL(url);
    const actual = parsed.port || (parsed.protocol === "https:" ? "443" : "80");
    if (String(actual) !== String(requiredPort)) {
      console.error(
        `CAP_SERVER_URL port is ${actual} but CAP_SERVER_PORT=${requiredPort}. ` +
          `Set CAP_SERVER_URL to include :${requiredPort} (e.g. https://host:${requiredPort}).`
      );
      process.exit(1);
    }
  } catch (e) {
    console.error(`Invalid CAP_SERVER_URL: ${e.message}`);
    process.exit(1);
  }
}

const env = loadEnv();
const requiredPort = (env.CAP_SERVER_PORT || "").trim();
const fallbackPort = requiredPort || "5001";
const serverUrl = normalizeServerUrl(env.CAP_SERVER_URL, fallbackPort);
if (requiredPort) {
  assertServerPort(serverUrl, requiredPort);
}
const isHttps = serverUrl.startsWith("https://");
const appId = env.CAP_APP_ID || "com.inertia.investment";
const appName = env.CAP_APP_NAME || "INERTIA";

const config = {
  appId,
  appName,
  webDir: "www",
  server: {
    url: serverUrl,
    cleartext: !isHttps,
    androidScheme: isHttps ? "https" : "http",
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 1500,
      launchAutoHide: true,
      backgroundColor: "#2563eb",
      showSpinner: false,
    },
    StatusBar: {
      style: "LIGHT",
      backgroundColor: "#2563eb",
    },
    Keyboard: {
      resize: "body",
      resizeOnFullScreen: true,
    },
  },
  android: {
    allowMixedContent: !isHttps,
  },
  ios: {
    contentInset: "automatic",
    scrollEnabled: true,
  },
};

const outPath = path.join(root, "capacitor.config.json");
fs.writeFileSync(outPath, JSON.stringify(config, null, 2) + "\n");
console.log(`Wrote ${outPath}`);
console.log(`  server.url = ${serverUrl}`);

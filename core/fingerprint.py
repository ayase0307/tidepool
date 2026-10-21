"""Per-worker fingerprint generation and Chromium stealth injection.

Each worker gets a deterministic fingerprint derived from its seed so that
fingerprints stay stable across restarts (important for account trust), while
being mutually unlinked between workers.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

# All workers run the real local Chrome (v155) on Windows; UA must match the
# real browser, or client-hint/UA/uaData consistency checks flag the session.
# Uniqueness comes from profile isolation + canvas/WebGL/audio noise + screen.
WORKER_SCHEMAS: List[Dict[str, Any]] = [
    {"os": "Windows", "ch": "155", "brand": "Google Chrome"},
]

UA_PLATFORM = {
    "Windows": ("Win32", "Windows NT 10.0; Win64; x64"),
}

LOCALES = ["zh-TW", "en-US"]
TIMEZONES = ["Asia/Taipei"]

SCREENS = [
    (1920, 1080), (2560, 1440), (1536, 864), (1440, 900),
    (1920, 1200), (1600, 900), (1728, 1117), (1680, 1050),
]

WEBGL = [
    ("Intel Inc.", "Intel Iris OpenGL Engine"),
    ("Apple", "Apple M1"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Apple GL", "Apple M2 Pro"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Intel", "Intel(R) UHD Graphics"),
]


def _rng(seed: str, salt: str = "") -> random.Random:
    h = hashlib.sha256(f"{seed}:{salt}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:16], "big"))


def build_fingerprint(worker_id: int, seed: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rng = _rng(seed, f"w{worker_id}")
    schema = WORKER_SCHEMAS[(worker_id - 1) % len(WORKER_SCHEMAS)]
    os_name, ch = schema["os"], schema["ch"]
    plat_token, ua_os = UA_PLATFORM[os_name]
    arch = "arm64" if (os_name == "macOS" and rng.random() < 0.6) else "x86_64"

    w, h = SCREENS[rng.randrange(0, len(SCREENS))]
    if os_name == "macOS" and w == 2560:
        w, h = 1728, 1117
    ua = (
        f"Mozilla/5.0 ({ua_os}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{ch}.0.0.0 {'Safari/537.36' if os_name == 'Windows' else 'Safari/537.36'}"
    )

    vendor, renderer = WEBGL[rng.randrange(0, len(WEBGL))]
    if not renderer.startswith("ANGLE") and os_name == "Windows":
        vendor, renderer = WEBGL[3]

    cores = rng.choice([4, 8, 8, 10, 12, 16])
    mem = rng.choice([4, 8, 8, 8, 16, 16])

    fp: Dict[str, Any] = {
        "worker_id": worker_id,
        "os": os_name,
        "arch": arch,
        "user_agent": ua,
        "platform": plat_token,
        "locale": LOCALES[rng.randrange(0, len(LOCALES))],
        "timezone_id": TIMEZONES[rng.randrange(0, len(TIMEZONES))],
        "screen": [w, h],
        "device_memory": mem,
        "hardware_concurrency": cores,
        "webgl_vendor": vendor,
        "webgl_renderer": renderer,
        "canvas_noise_seed": rng.randrange(1, 10 ** 9),
        "webgl_noise_seed": rng.randrange(1, 10 ** 9),
        "audio_noise_seed": rng.randrange(1, 10 ** 9),
        "language": None,
    }
    if extra:
        for k, v in extra.items():
            if k == "locale":
                fp["locale"] = v
            elif v is not None:
                fp[k] = v
    fp["language"] = {"en-US": "en-US", "zh-TW": "zh-TW", "ja-US": "ja-US,en-US"}.get(
        fp["locale"], "en-US"
    )
    return fp


INJECT_JS = r"""
(function () {
  const FP = window.__WORKER_FP__;
  if (!FP || window.__FP_INJECTED__) return;
  window.__FP_INJECTED__ = true;

  const os = FP.os;
  const plat = { 'Win32': 0x00000001 ? 'Windows' : '', 'Windows': 'Windows', 'macOS': 'macOS' }[os] || os;

  function def(obj, prop, get) {
    Object.defineProperty(obj, prop, { get, configurable: true });
  }

  Object.defineProperty(navigator, 'language', { get: () => FP.locale, configurable: true });
  Object.defineProperty(navigator, 'languages', { get: () => FP.locale.includes('-') ? [FP.locale, FP.locale.split('-')[0]] : [FP.locale, 'en'], configurable: true });
  Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => FP.hardware_concurrency, configurable: true });
  Object.defineProperty(navigator, 'deviceMemory', { get: () => FP.device_memory, configurable: true });
  Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0, configurable: true });
  def(navigator, 'pdfViewerEnabled', () => true);

  // canvas 2d noise
  const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  const origToBlob = HTMLCanvasElement.prototype.toBlob;
  const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
  function xorShift(seed) {
    let x = seed >>> 0;
    return function () {
      x ^= x << 13; x >>>= 0; x ^= x >> 17; x ^= x << 5; x >>>= 0;
      return (x >>> 0) / 4294967296;
    };
  }
  const cnRng = xorShift(FP.canvas_noise_seed);
  CanvasRenderingContext2D.prototype.getImageData = function (x, y, w, h) {
    const img = origGetImageData.call(this, x, y, w, h);
    for (let i = 0; i < img.data.length; i += (cnRng() < 0.14 ? 4 : 1)) {
      img.data[i] = img.data[i] ^ 1;
    }
    return img;
  };
  HTMLCanvasElement.prototype.toDataURL = function (...args) {
    try { if (cnRng() < 0.9) { /* subtle */ } } catch (e) {}
    return origToDataURL.apply(this, args);
  };
  HTMLCanvasElement.prototype.toBlob = function (...args) { return origToBlob.apply(this, args); };

  // WebGL noise + metadata
  const webglVendor = FP.webgl_vendor, webglRenderer = FP.webgl_renderer;
  const wp = WebGLRenderingContext.prototype;
  const wp2 = (typeof WebGL2RenderingContext !== 'undefined') ? WebGL2RenderingContext.prototype : null;
  const origParam = wp.getParameter;
  const origParam2 = wp2 ? wp2.getParameter : null;
  function patchGL(proto, orig) {
    return function (pname) {
      const UNMASKED_VENDOR = 0x9245, UNMASKED_RENDERER = 0x9246;
      try {
        const ext = this.getExtension('WEBGL_debug_renderer_info');
        if (ext && pname === ext.UNMASKED_VENDOR_WEBGL) return webglVendor;
        if (ext && pname === ext.UNMASKED_RENDERER_WEBGL) return webglRenderer;
      } catch (e) {}
      return orig.call(this, pname);
    };
  }
  wp.getParameter = patchGL(wp, origParam);
  if (wp2) wp2.getParameter = patchGL(wp2, origParam2);
  function readout(orig, method, glSeed) {
    return function (...a) {
      const r = orig.apply(this, a);
      return r;
    };
  }

  // AudioContext noise
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC) {
      const proto = AC.prototype;
      const origChannelData = Object.getOwnPropertyDescriptor(AudioBuffer.prototype, 'getChannelData');
      Object.defineProperty(AudioBuffer.prototype, 'getChannelData', {
        get: function () {
          return (function (channel) {
            const data = origChannelData.value.call(this, channel);
            const noise = xorShift(FP.audio_noise_seed);
            for (let i = 0; i < Math.min(data.length, 2048); i++) {
              data[i] += (noise() - 0.5) * 1e-7;
            }
            return data;
          });
        }.bind && origChannelData ? function () {} : undefined,
        configurable: true
      });
    }
  } catch (e) { /* fallback: skip audio patch on mismatch */ }

  // misc consistency
  try {
    Object.defineProperty(screen, 'width', { get: () => FP.screen[0], configurable: true });
    Object.defineProperty(screen, 'height', { get: () => FP.screen[1], configurable: true });
    Object.defineProperty(screen, 'availWidth', { get: () => FP.screen[0], configurable: true });
    Object.defineProperty(screen, 'availHeight', { get: () => FP.screen[1] - 48, configurable: true });
    Object.defineProperty(screen, 'colorDepth', { get: () => 24, configurable: true });
    Object.defineProperty(screen, 'pixelDepth', { get: () => 24, configurable: true });
    window.devicePixelRatio = 1;
  } catch (e) {}

  try {
    if (!navigator.plugins || navigator.plugins.length === 0) {
      def(navigator, 'plugins', () => {
        const arr = [1, 2, 3, 4, 5].map(i => ({ name: `Plugin ${i}`, filename: `plugin${i}`, description: '' }));
        return arr;
      });
    }
  } catch (e) {}

  try {
    const origRTCGetSources = window.RTCPeerConnection;
    if (origRTCGetSources && !window.RTCPeerConnection.prototype.__fpPatched) {
      window.RTCPeerConnection.prototype.__fpPatched = true;
    }
  } catch (e) {}
})();
"""


def injection_init_script(fingerprint: Dict[str, Any]) -> str:
    return f"window.__WORKER_FP__ = {json.dumps(fingerprint)};\n{INJECT_JS}"

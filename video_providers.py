# video_providers.py
# Ported from Lumen (youtube-automation-agent) utils/video-providers.js
# Chain: Seedance (Replicate, real AI video clip) -> OpenAI image (real AI still,
# Ken Burns motion applied at assembly) -> placeholder (flagged SIMULATED).
# Orientation is PER-PRODUCTION now: "vertical" (9:16) / "landscape" (16:9),
# falling back to the server-wide VIDEO_ASPECT_RATIO env var when not set.
import os
import time
import requests


def _redact(msg: str) -> str:
    import re
    msg = re.sub(r'Bearer\s+[A-Za-z0-9._~-]+', 'Bearer [redacted]', str(msg))
    msg = re.sub(r'(api[_-]?key|token|secret)=([^\s&]+)', r'\1=[redacted]', msg, flags=re.I)
    return msg[:400]


def provider_status() -> dict:
    """Report which providers are configured (for /health and debugging)."""
    return {
        "seedance": bool(os.getenv("REPLICATE_API_TOKEN") or os.getenv("REPLICATE_API_KEY")),
        "openai_image": bool(os.getenv("OPENAI_API_KEY")),
        "placeholder": True,
        "selected_order": os.getenv("VIDEO_PROVIDER_ORDER", "seedance,openai_image,placeholder"),
    }


def _is_vertical(orientation=None) -> bool:
    """orientation: 'vertical' / 'landscape' / None (-> VIDEO_ASPECT_RATIO env)."""
    if orientation:
        return str(orientation).strip().lower() in ("vertical", "9:16", "9x16")
    return os.getenv("VIDEO_ASPECT_RATIO", "16:9").strip() in ("9:16", "9x16", "vertical")


# ---------- Seedance via Replicate (real video clip) ----------

def _seedance_clip(prompt: str, output_path: str, duration: float, orientation=None) -> bool:
    token = os.getenv("REPLICATE_API_TOKEN") or os.getenv("REPLICATE_API_KEY")
    if not token:
        return False
    try:
        import replicate
        client = replicate.Client(api_token=token)
        model = os.getenv("SEEDANCE_MODEL", "bytedance/seedance-2.5")
        clip_seconds = max(4, min(10, int(round(duration)) or 5))
        prediction = client.predictions.create(
            model=model,
            input={
                "prompt": prompt[:1900],
                "duration": clip_seconds,
                "resolution": os.getenv("VIDEO_RESOLUTION", "720p"),
                "aspect_ratio": "9:16" if _is_vertical(orientation) else "16:9",
                "output_format": "mp4",
            },
        )
        # Bounded wait: poll up to ~10 minutes, then give up (fallback to next provider).
        # prediction.wait() alone can hang forever when Replicate queues the job.
        deadline = time.time() + 600
        while time.time() < deadline:
            prediction.reload()
            if prediction.status in ("succeeded", "failed", "canceled"):
                break
            time.sleep(5)
        if prediction.status != "succeeded":
            print(f"[Video] Seedance did not succeed (status={prediction.status}): {_redact(getattr(prediction, 'error', ''))}")
            return False
        out = prediction.output
        url = None
        if isinstance(out, str):
            url = out
        elif isinstance(out, list) and out:
            url = str(out[0])
        elif hasattr(out, "url"):
            url = out.url() if callable(out.url) else out.url
        if hasattr(out, "read"):
            with open(output_path, "wb") as f:
                f.write(out.read())
            return True
        if not url:
            print("[Video] Seedance succeeded but returned no URL")
            return False
        r = requests.get(str(url), timeout=120)
        if r.status_code != 200 or len(r.content) < 10000:
            print(f"[Video] Seedance download failed: HTTP {r.status_code}")
            return False
        with open(output_path, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:
        print(f"[Video] Seedance exception: {_redact(e)}")
        return False


# ---------- OpenAI image (real AI still; Ken Burns at assembly) ----------

def _openai_image(prompt: str, output_path: str, orientation=None) -> bool:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return False
    try:
        vertical = _is_vertical(orientation)
        aspect = "9:16 vertical" if vertical else "16:9"
        size = "1024x1536" if vertical else "1536x1024"
        r = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
                "prompt": f"cinematic, ethereal, {aspect} video background frame: {prompt}"[:3900],
                "size": size,
                "n": 1,
            },
            timeout=120,
        )
        if r.status_code != 200:
            print(f"[Video] OpenAI image ERROR {r.status_code}: {_redact(r.text)}")
            return False
        data = r.json()["data"][0]
        import base64
        if data.get("b64_json"):
            with open(output_path, "wb") as f:
                f.write(base64.b64decode(data["b64_json"]))
            return True
        if data.get("url"):
            img = requests.get(data["url"], timeout=60)
            if img.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(img.content)
                return True
        return False
    except Exception as e:
        print(f"[Video] OpenAI image exception: {_redact(e)}")
        return False


# ---------- Public entry ----------

def generate_scene_visual(prompt: str, out_base: str, duration: float, orientation=None):
    """
    Try providers in VIDEO_PROVIDER_ORDER.
    Returns (file_path, provider_id, simulated_bool).
    out_base has no extension; extension depends on provider (.mp4 clip or .png still).
    orientation: 'vertical' / 'landscape' / None (-> env VIDEO_ASPECT_RATIO).
    """
    prompt = (prompt or "Answers in Faith").strip()
    order = [p.strip() for p in os.getenv(
        "VIDEO_PROVIDER_ORDER", "seedance,openai_image,placeholder").split(",")]

    for provider in order:
        if provider == "seedance":
            path = out_base + ".mp4"
            if _seedance_clip(prompt, path, duration, orientation):
                return path, "seedance", False
        elif provider in ("openai_image", "image"):
            path = out_base + ".png"
            if _openai_image(prompt, path, orientation):
                return path, "openai_image", False
        elif provider == "placeholder":
            break  # handled by caller (pipeline._generate_placeholder_visual)

    return None, "placeholder", True

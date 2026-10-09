"""
Record the demo video: a browser clicks through the app with the mock data, with captions on screen and a voice
reading each caption.

    1. Start the app on an empty demo database (PowerShell):
         $env:INVOICE_DB_PATH = "data\\demo_video.db"; python -m streamlit run app.py --server.port 8502 --server.fileWatcherType none
    2. python demo/record_demo_video.py
       needs: pip install playwright imageio-ffmpeg edge-tts; python -m playwright install chromium

Writes demo/demo_video.mp4. The steps follow explain.md, section 7. The narration uses Microsoft's online neural voices
(edge-tts): the caption texts are sent to Microsoft to be spoken. Pick another voice with DEMO_VOICE
(python -m edge_tts --list-voices), e.g. en-IN-PrabhatNeural.
"""
import ast
import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time

import edge_tts
import imageio_ffmpeg
from playwright.sync_api import Page, sync_playwright

URL = os.environ.get("DEMO_URL", "http://localhost:8502")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIZE = {"width": 1440, "height": 900}
VOICE = os.environ.get("DEMO_VOICE", "en-US-AndrewNeural")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
AUDIO_DIR = os.path.join(tempfile.gettempdir(), "invoice-demo-narration")

CAPTION_JS = """text => {
    let el = document.getElementById('demo-caption');
    if (!el) {
        el = document.createElement('div');
        el.id = 'demo-caption';
        el.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:99999;max-width:1100px;' +
            'background:rgba(15,23,42,.92);color:#fff;font:600 24px/1.35 system-ui,sans-serif;padding:16px 28px;' +
            'border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.35);text-align:center;pointer-events:none';
        document.body.appendChild(el);
    }
    el.style.display = text ? 'block' : 'none';
    el.textContent = text;
}"""


SPOKEN = [(r"(\d)x\b", r"\1 times"), (r"(\d+)-(\d+)%", r"\1 to \2 percent"), (r"\bdd/mm\b", "day-first"),
          (r"\bPO\b", "purchase order"), (r"\bOCR\b", "O C R"), (r"\.\.\.", ",")]
SPOKEN_LINES = []  # (time the caption appeared, audio file), mixed into the video at the end


def spoken(text: str) -> str:
    """The caption as it should be read aloud: "6.6x" -> "6.6 times", "97-100%" -> "97 to 100 percent"."""
    for pattern, repl in SPOKEN:
        text = re.sub(pattern, repl, text)
    return text


def audio_file(text: str) -> str:
    return os.path.join(AUDIO_DIR, hashlib.sha1(f"{VOICE}|{spoken(text)}".encode()).hexdigest()[:16] + ".mp3")


def duration(path: str) -> float:
    out = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True).stderr
    h, m, sec = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out).groups()
    return int(h) * 3600 + int(m) * 60 + float(sec)


def prepare_narration():
    """Speak every caption in this script once, before recording, so the recording never waits for the voice service."""
    os.makedirs(AUDIO_DIR, exist_ok=True)
    texts = [ast.literal_eval(m) for m in re.findall(r'say\(page, ("(?:[^"\\]|\\.)*")', open(__file__, encoding="utf-8").read())]
    for text in filter(None, texts):
        if not os.path.exists(audio_file(text)):
            asyncio.run(edge_tts.Communicate(spoken(text), VOICE, rate="-5%").save(audio_file(text)))


def say(page: Page, text: str, seconds: float = 4.0):
    """Show a caption and read it aloud; it stays up for `seconds` or until the voice has finished, whichever is longer."""
    page.evaluate(CAPTION_JS, text)
    if text:
        SPOKEN_LINES.append((time.monotonic(), audio_file(text)))
        seconds = max(seconds, duration(audio_file(text)) + 0.8)
    page.wait_for_timeout(int(seconds * 1000))


def idle(page: Page, timeout: int = 120_000):
    """Wait until Streamlit has finished running the script."""
    page.wait_for_timeout(600)
    page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=timeout)
    page.wait_for_timeout(400)


def tab(page: Page, name: str):
    page.get_by_role("tab", name=name).click()
    idle(page)


def upload(page: Page, label: str, path: str):
    box = page.locator('[data-testid="stFileUploader"]').filter(has_text=label).first
    box.locator('input[type="file"]').set_input_files(os.path.join(ROOT, path))
    idle(page)


def click(page: Page, name: str, exact: bool = True):
    page.get_by_role("button", name=name, exact=exact).locator("visible=true").first.click()
    idle(page)


def scroll_to(page: Page, text: str):
    page.get_by_text(text, exact=False).locator("visible=true").first.scroll_into_view_if_needed()
    page.wait_for_timeout(500)


def scroll_top(page: Page):
    page.evaluate("() => document.querySelectorAll('section, [data-testid=\"stMain\"], [data-testid=\"stAppViewContainer\"]')"
                  ".forEach(e => e.scrollTo(0, 0))")
    page.wait_for_timeout(400)


def run(page: Page):
    page.goto(URL)
    page.get_by_role("tab", name="Company records").wait_for(timeout=120_000)
    idle(page)
    say(page, "Invoice Checker: catches duplicate payments and overpayments before an invoice is paid", 5)
    say(page, "Everything runs on the company's own machine. This is an empty demo database.", 4)

    page.get_by_role("textbox", name="Your name").fill("Tushar")
    page.keyboard.press("Enter")
    idle(page)
    say(page, "Every check is logged with the name of the person who ran it", 3.5)

    # 1. History
    tab(page, "Company records")
    say(page, "Step 1: load the company's past paid invoices, exported from any accounting system", 4.5)
    upload(page, "Company records", "demo/company_records.csv")
    say(page, "Columns like 'Invoice No' and 'Supplier' are recognized automatically; dd/mm dates are detected", 5)
    click(page, "Import 158 records")
    say(page, "158 invoices from 8 vendors imported", 3)
    upload(page, "Company records", "demo/company_records.csv")
    click(page, "Import 158 records")
    say(page, "Importing the same file again adds nothing: every row is already on record", 4.5)
    click(page, "Audit records")
    say(page, "The audit scans the history for money already lost: 4 high-risk payments", 4)
    scroll_to(page, "Highest-risk records")
    say(page, "A double payment, a copy retyped in another system, a renamed vendor, and 6.6x the usual amount", 6.5)

    # 2. Batch
    scroll_top(page)
    tab(page, "Check a batch")
    say(page, "Step 2: check this week's invoices before paying them", 4)
    upload(page, "Invoices to check", "demo/invoices_to_check.csv")
    click(page, "Check 15 invoices")
    say(page, "9 held as suspicious, 2 for review, 4 fine to pay", 4.5)
    scroll_to(page, "Download results (CSV)")
    say(page, "Each verdict has a reason: exact copies, retyped numbers, 'The' added to the name, an O read as a 0...", 6.5)
    say(page, "...tax added to a paid invoice number, 6x the vendor's usual amount, and a burst of 3 invoices on one PO", 6.5)
    say(page, "A misspelled vendor gets a suggestion instead of a false alarm", 4)

    # 3. Photo
    scroll_top(page)
    tab(page, "Check one invoice")
    say(page, "Step 3: a photo of a receipt", 3)
    upload(page, "Receipt or invoice", "demo/receipt_duplicate.png")
    page.get_by_text("Fields read from the document").wait_for(timeout=120_000)
    say(page, "OCR reads vendor, invoice number, date and total locally; the user confirms them", 5)
    click(page, "Check")
    scroll_to(page, "Score details")
    say(page, "SUSPICIOUS: this receipt was already paid, and the matching record is shown", 5)
    say(page, "A suspicious invoice can't be added to the company records by mistake", 4)

    scroll_top(page)
    upload(page, "Receipt or invoice", "demo/receipt_duplicate_low_quality.jpg")
    page.get_by_text("Fields read from the document").wait_for(timeout=120_000)
    click(page, "Check")
    scroll_to(page, "Score details")
    say(page, "A heavily compressed JPEG: the image check honestly says 'not checked' instead of 'clean'", 5.5)

    scroll_top(page)
    say(page, "Last: a receipt whose total was edited (a real forgery from a research dataset)", 0.5)
    upload(page, "Receipt or invoice", "data/samples/3_forged_receipt.png")
    page.get_by_text("Fields read from the document").wait_for(timeout=120_000)
    page.wait_for_timeout(2500)
    click(page, "Check")
    scroll_to(page, "The image may have been edited")
    say(page, "REVIEW: the vendor is new, but the image-tamper model says the receipt was probably edited", 5.5)
    scroll_to(page, "Most suspicious area")
    say(page, "It outlines the edited area so the reviewer knows where to look", 5.5)

    # 4. Log
    scroll_top(page)
    tab(page, "Company records")
    scroll_to(page, "Recent checks")
    say(page, "Every check, its verdict and who ran it are kept in the log", 4.5)
    say(page, "Duplicates caught: 97-100% on real receipts, tested with 10-fold cross-validation", 5)
    say(page, "", 0.5)


if __name__ == "__main__":
    prepare_narration()
    tmp = tempfile.mkdtemp()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=SIZE, record_video_dir=tmp, record_video_size=SIZE)
        page = context.new_page()
        started = time.monotonic()
        try:
            run(page)
        except Exception:
            page.screenshot(path=os.path.join(tmp, "..", "demo_failure.png"), full_page=True)
            raise
        finally:
            video = page.video.path()
            context.close()
            browser.close()
    out = os.path.join(HERE, "demo_video.mp4")
    skip = max(0.0, SPOKEN_LINES[0][0] - started - 0.5) if SPOKEN_LINES else 0.0  # cut the loading screen
    inputs, delays = [], []
    for i, (at, path) in enumerate(SPOKEN_LINES, start=1):
        inputs += ["-i", path]
        delays.append(f"[{i}:a]adelay={int((at - started - skip) * 1000)}:all=1[a{i}]")
    mix = ";".join(delays) + ";" + "".join(f"[a{i}]" for i in range(1, len(SPOKEN_LINES) + 1)) + \
        f"amix=inputs={len(SPOKEN_LINES)}:normalize=0[voice]"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", f"{skip:.2f}", "-i", video, *inputs,
                    "-filter_complex", mix, "-map", "0:v", "-map", "[voice]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", out], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print("Wrote", out)

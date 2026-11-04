"""Render preview PNGs directly from unchanged vector PDFs using Xpdf/Poppler and Pillow."""
import hashlib,subprocess
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]
def main():
    (ROOT/"tmp/qa").mkdir(parents=True, exist_ok=True)
    for pdf in sorted((ROOT/"figures").rglob("*.pdf")):
        png=pdf.with_suffix(".png")
        old={"dimensions":None,"bytes":0}
        if png.exists():
            with Image.open(png) as im: old={"dimensions":list(im.size),"bytes":png.stat().st_size}
        before=hashlib.sha256(pdf.read_bytes()).hexdigest()
        prefix=ROOT/"tmp/qa"/pdf.stem
        subprocess.run(["pdftoppm","-r","450","-f","1","-l","1","-aa","yes","-aaVector","yes",str(pdf),str(prefix)],check=True,capture_output=True)
        candidates=sorted(prefix.parent.glob(prefix.name+"-*.ppm"))
        if len(candidates)!=1:raise ValueError("Unexpected PDF page output")
        with Image.open(candidates[0]) as im:
            im.save(png,format="PNG",optimize=True,dpi=(450,450))
            dimensions=list(im.size)
        # Temp deletion is restricted to this exact generated PPM.
        ppm=candidates[0].resolve()
        if ppm.parent != (ROOT/"tmp/qa").resolve():raise ValueError("Unexpected temporary location")
        ppm.unlink()
        if hashlib.sha256(pdf.read_bytes()).hexdigest()!=before:raise ValueError("PDF mutated")
        print(png.name,old["dimensions"],"->",dimensions,png.stat().st_size,flush=True)
if __name__=="__main__":main()

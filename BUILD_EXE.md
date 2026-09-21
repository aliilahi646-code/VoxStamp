# VoxStamp ko Windows .exe Banane Ka Tareeqa

Ye ek dafa karne wala kaam hai. Iske baad tumhare paas `VoxStamp.exe` hoga jo
kisi bhi Windows PC par double-click karke chalega (Python install kiye
bina) -- bilkul CapCut jaisa, apni window mein.

## Zaroori: pehle confirm karo

Ye files `Downloads\Transcribe` folder mein honi chahiye is guide se pehle:
- `app.py`, `desktop_app.py` (dono is baar wale)
- `static\` (poora folder, icons sameet)
- `requirements.txt`, `requirements-desktop.txt`

## Step 1: Extra software install karo

Command Prompt mein (`cd Downloads\Transcribe` karne ke baad):
```
pip install -r requirements.txt
pip install -r requirements-desktop.txt
```
(Ye `pywebview` aur `pyinstaller` install karega -- .exe banane ke tools)

## Step 2: Pehle test karo (bina .exe banaye)

Confirm karo ke desktop version sahi chal rahi hai:
```
python desktop_app.py
```
Ek nayi window khulni chahiye (browser nahi) jisme VoxStamp ho. Agar ye
sahi khul jaye, band kar do (window ka X) aur agle step par jao.

## Step 3: .exe banao

Isi folder mein ye **poora command ek line mein** chalao (copy-paste karo):

```
pyinstaller --onefile --name VoxStamp --icon=static\icons\icon.ico --add-data "static;static" --collect-all faster_whisper --collect-all ctranslate2 --collect-all edge_tts --collect-all tokenizers --collect-all authlib desktop_app.py
```

**Ye 3-10 minute le sakta hai** (bara process hai, ruk mat jana beech mein).
Command khatam hone ka wait karo -- jab wapas `C:\...\Transcribe>` prompt
dikhe to matlab ho gaya.

## Step 4: .exe test karo

1. Isi folder ke andar ek naya **`dist`** folder banega
2. Us mein `VoxStamp.exe` hogi
3. Usay **double-click** karo
4. Ek Command Prompt jaisi kali window khulegi (thodi der ke liye -- ye
   background mein chal raha hai), phir VoxStamp ki asal window khulni
   chahiye

Agar sahi chal jaye -- mubarak ho, ye ab ek **asal Windows app** hai. Isay
kisi ko bhi bhej sakte ho, wo Python install kiye bina chala sakta hai
(bas unke PC par bhi **ffmpeg installed** hona chahiye, jaisa humne tumhare
liye kiya tha).

## Console window hide karna (optional, agar sab sahi chal raha ho)

Upar wale step 4 mein jo kali Command Prompt window dikhti hai, wo
error-checking ke liye achi hai (pehli baar). Ek baar sab sahi chal jaye,
to usay hide karne ke liye **dobara** ye command chalao (`--windowed` add
karke):

```
pyinstaller --onefile --windowed --name VoxStamp --icon=static\icons\icon.ico --add-data "static;static" --collect-all faster_whisper --collect-all ctranslate2 --collect-all edge_tts --collect-all tokenizers --collect-all authlib desktop_app.py
```

Ab `dist\VoxStamp.exe` bilkul saaf khulegi, koi kali window nahi dikhegi.

## Zaroori baatein

- **File size:** ye .exe bara hoga (200MB - 1GB ke beech), kyunki ismein
  transcription engine ki saari zaroori files bundled hain. Ye normal hai.
- **ffmpeg:** .exe khud ffmpeg nahi le kar jaati -- jis bhi PC par chalao,
  wahan ffmpeg installed aur PATH mein hona chahiye (jaisa humne tumhare
  liye kiya tha).
- **Internet:** pehli baar chalane par model download hoga (jaisa web
  version mein hota hai), aur Voiceover tool ko hamesha internet chahiye
  (Microsoft ki voice service use karta hai).
- **Dobara build karna:** jab bhi `app.py` ya `static` files update karo
  aur .exe mein bhi wo changes chahiye hon, Step 3 wala command **dobara**
  chalana hoga -- purani `dist\VoxStamp.exe` khud update nahi hoti.

# Online Publish Karne Ka Tareeqa (Render.com - Free)

## Step 1: Pehle apne PC par test karo
1. Terminal/Command Prompt kholo, is folder mein jao:
   ```
   cd path\to\this\folder
   ```
2. Install karo:
   ```
   pip install -r requirements.txt
   ```
3. Chalao:
   ```
   python app.py
   ```
4. Browser mein kholo: `http://localhost:5000`
5. Test karo ke sahi chal raha hai.

## Step 2: GitHub par upload karo
1. https://github.com par free account banao (agar nahi hai)
2. New repository banao (e.g. "exact-transcriber")
3. Is folder ki teeno files (`app.py`, `requirements.txt`, ye README) us repo mein upload karo
   (GitHub website par "Add file" > "Upload files" se bhi ho jata hai, git command line zaroori nahi)

## Step 3: Render.com par deploy karo
1. https://render.com par free account banao
2. Dashboard mein "New +" > "Web Service" dabao
3. Apna GitHub repo connect karo (jo step 2 mein banaya)
4. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python app.py`
   - **Instance Type:** Free
5. "Create Web Service" dabao
6. 5-10 minute wait karo -- Render tumhe ek public URL dega jaise:
   `https://exact-transcriber.onrender.com`

## Ye URL ab tumhara "published" tool hai
- Kisi bhi browser se, kisi bhi device se khol sakte ho
- Client ko bhi bhej sakte ho seedha
- Apni website mein bhi link kar sakte ho

## Zaroori baatein
- Free plan par Render "sleep" mode mein chala jata hai jab kuch der use na ho -- pehli request thodi slow (30-60 sec) hogi jab wapas jagega.
- Bara audio file (jaise 1+ hour) process karne mein time lagta hai -- free server par CPU limited hoti hai, "medium" ya "small" model use karo speed ke liye.
- Agar zyada traffic/speed chahiye ho, Render ka paid plan ya GPU wala hosting (RunPod, Modal) dekh sakte ho -- us waqt bata dena, wo setup bhi kar dunga.

## "Sign in with Google" enable karna (optional)

Bina Google setup kiye bhi email/password login pehle se kaam karta hai. Agar
"Continue with Google" button bhi chahiye (password ke bina login), ye steps
follow karo:

### 1) Google Cloud Console mein project banao
1. https://console.cloud.google.com par jao, Google account se login karo
2. Upar "Select a project" > "New Project" > naam do (e.g. "VoxStamp") > Create

### 2) OAuth consent screen set karo
1. Left menu mein "APIs & Services" > "OAuth consent screen"
2. "External" select karo > Create
3. App name: "VoxStamp", apna email daalo jahan pucha jaye > Save and Continue
   (agle steps sab default rehne do, "Save and Continue" karte jao)

### 3) OAuth Client ID banao
1. "APIs & Services" > "Credentials" > "Create Credentials" > "OAuth client ID"
2. Application type: **Web application**
3. Name: "VoxStamp Web"
4. **Authorized redirect URIs** mein ye DONO add karo (local aur online, dono):
   ```
   http://localhost:5000/auth/google/callback
   https://tumhara-render-link.onrender.com/auth/google/callback
   ```
   (dusri wali mein apna asal Render URL daalna, jo tumhe deployment ke baad mila tha)
5. "Create" dabao -- ab ek **Client ID** aur **Client Secret** dikhega, dono copy kar lo

### 4) In values ko environment variables ke taur par set karo

**Local (apne PC) par:**
Command Prompt mein `python app.py` chalane se PEHLE ye likho (har naye session mein karna padega):
```
set GOOGLE_CLIENT_ID=yahan_apna_client_id_paste_karo
set GOOGLE_CLIENT_SECRET=yahan_apna_client_secret_paste_karo
python app.py
```

**Render (online) par:**
1. Render dashboard mein apni service kholo
2. Left menu mein "Environment" par jao
3. "Add Environment Variable" dabao, do variables add karo:
   - Key: `GOOGLE_CLIENT_ID`, Value: apna client ID
   - Key: `GOOGLE_CLIENT_SECRET`, Value: apna client secret
4. "Save Changes" -- Render khud restart kar dega

Ab login page par "Continue with Google" button dikhne lagega.


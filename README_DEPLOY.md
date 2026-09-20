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

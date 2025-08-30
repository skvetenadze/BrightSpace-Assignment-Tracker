# Brightspace Assignment Tracker

A Python-based assignment tracker that fetches assignments from **Brightspace iCal feeds** and automatically uploads them into **Google Sheets**.  
Useful for staying on top of coursework — assignments are refreshed every 30 minutes and only those due in the next 2 weeks are shown.

---

## 🚀 Features
- Pulls assignments directly from Brightspace calendars (.ics feeds).
- Supports multiple course feeds at once.
- Normalizes due dates to your local timezone.
- De-duplicates assignments already in Google Sheets.
- Updates Google Sheets with:
  - Assignment title  
  - Subject/Course  
  - Status (default: Not Started)  
  - Due Date  
  - Days Left  
  - Priority Level  

---

## ⚙️ Setup

1. Clone the repo
```bash
git clone https://github.com/<your-username>/Brightspace-Assignment-Tracker.git
cd Brightspace-Assignment-Tracker
```
2. Install dependencies
pip install -r requirements.txt

3. Configure environment variables

Create a .env file in the project root:

SHEET_NAME=Assignment Tracker
BRIGHTSPACE_ICS_URLS=https://brightspace.xxxx
GOOGLE_CREDENTIALS_FILE=credentials.json

LOCAL_TZ=America/New_York
WINDOW_DAYS=14
POLL_SECONDS=1800

4. Set up Google credentials

In Google Cloud Console, create a Service Account and download its credentials.json.

Share your target Google Sheet with the service account’s email (Editor role).

5. Run the tracker
python brightspace.py


The script will check Brightspace every 30 minutes and push new assignments into Google Sheets.

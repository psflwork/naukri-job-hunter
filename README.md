# Naukri Job Hunter

Finds jobs and side gigs on [naukri.com](https://www.naukri.com) that match **your resume**, ranks
them with a score out of 100, and helps you apply. Runs on your own computer, in your web browser.
No coding needed.

- Full-time jobs, or side gigs (part-time, freelance, contract)
- Filter by skills, location, work mode (remote / hybrid / office) and job type
- Apply to the jobs you tick, or get recruiter emails and phone numbers to send your resume yourself
- Your resume, login and results never leave your computer

## Get started

1. **Install** [Google Chrome](https://www.google.com/chrome/) and
   [Python](https://www.python.org/downloads/) (free). On Windows, tick **"Add python.exe to PATH"**
   in the Python installer.
2. **Download** this tool: green **Code** button → **Download ZIP**, then unzip it.
3. **Double-click** `START-HERE-Windows.bat` (Windows) or `START-HERE-Mac.command` (Mac).
   The first start takes a few minutes. Then the app opens in your browser. Keep the small black
   window open while you use it.
4. In **What I'm looking for**, upload your resume, check the job titles and location, and click
   **Save**.

<details>
<summary>Windows or Mac blocks the file?</summary>

- **Windows** ("Windows protected your PC"): click **More info** → **Run anyway**.
- **Mac**: open **System Settings → Privacy & Security**, scroll down and click **Open Anyway**.
  If it still won't open, type `bash ` in the Terminal app, drag the file into the window and press Enter.

</details>

## Using it

| Tab | What it does |
|---|---|
| **1. Find jobs** | Searches Naukri in a Chrome window (5-20 minutes; don't close it) |
| **2. My matches** | Ranked matches. Tick the ones you like and click **Apply to ticked jobs** |
| **3. Recruiter contacts** | Published recruiter emails and phones, with a **Draft email** button |
| **What I'm looking for** | Resume, job titles, skills, location, work mode, job type, experience |
| **Naukri login** | Save your login (optional) or sign in now |

Switch between **Regular jobs** and **Side gigs** at the top. Next time, just double-click the
START-HERE file again.

<details>
<summary>Common questions</summary>

- **Is my password safe?** It's saved only on your computer and used only to sign in to Naukri.
- **Will it apply without asking?** No. It applies only to jobs you tick.
- **OTP or captcha?** Complete it in the Chrome window; you stay signed in afterwards.
- **No jobs found?** Add more job titles, clear the location, or untick work modes.
- **Page says it can't connect?** The app was stopped; double-click START-HERE again.

</details>

## For developers

```bash
git clone https://github.com/psflwork/naukri-job-hunter.git && cd naukri-job-hunter
./run.sh web     # browser app  |  ./run.sh menu  terminal menu  |  ./run.sh --help  all commands
```

It also works as an **AI agent** (MCP server for Cursor, Claude Desktop and others). See the
[advanced guide](docs/advanced.md) for the command line, AI agent setup, configuration and how it
works.

## Disclaimer

Unofficial tool, not affiliated with Naukri.com / Info Edge. Naukri's terms don't allow automated
use; heavy auto-applying can get an account flagged. Review jobs before applying and use it at your
own risk. [MIT license](LICENSE).

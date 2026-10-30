import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import gspread
from bs4 import BeautifulSoup
from curl_cffi import requests

# CONFIGURATION
MAX_WORKERS = 10          # Number of simultaneous requests
BATCH_SIZE = 20           # Number of rows written to Google Sheets at once
MAX_ROWS_PER_RUN = 100    # Limit per GitHub Action run to prevent timeouts

def main():
    secret_key = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    if not secret_key:
        raise ValueError("GCP_SERVICE_ACCOUNT_KEY environment variable is missing.")

    gcp_credentials = json.loads(secret_key)
    gc = gspread.service_account_from_dict(gcp_credentials)

    # Open target Google Sheet (You can replace with gc.open_by_key("YOUR_SHEET_ID") if preferred)
    sheet = gc.open("Homely Agents Data").sheet1

    headers = ["URL", "Name", "Agency", "Phone", "Email", "Image URL", "Status"]
    existing_headers = sheet.row_values(1)
    if not existing_headers or existing_headers[0] != "URL":
        sheet.update(range_name="D1:J1", values=[headers])

    # Fetch data starting from Column D (index 4) and Column J (index 10 for Status)
    urls_in_col_d = sheet.col_values(4)[1:]
    statuses_in_col_j = sheet.col_values(10)[1:]

    if not urls_in_col_d:
        print("No URLs found in Column D to process.")
        return

    # Identify rows that need processing (smart resume)
    tasks = []
    start_row = 2
    for idx, url in enumerate(urls_in_col_d):
        current_row = start_row + idx
        url_clean = url.strip()

        if not url_clean.startswith("http"):
            continue

        status = statuses_in_col_j[idx] if idx < len(statuses_in_col_j) else ""
        if status == "Scraped":
            continue  # Skip already completed rows

        tasks.append((current_row, url_clean))

    if not tasks:
        print("All URLs have already been scraped!")
        return

    # Limit rows per run to stay well within GitHub Action limits
    current_batch_tasks = tasks[:MAX_ROWS_PER_RUN]
    print(f"Starting high-speed concurrent scrape for {len(current_batch_tasks)} URLs using {MAX_WORKERS} threads...")

    # Shared session for curl_cffi
    session = requests.Session(impersonate="chrome120")

    def scrape_single_url(task):
        row_num, url = task
        print(f"Scraping Row {row_num}: {url}")
        row_data = extract_agent_data(session, url)
        return row_num, {
            "range": f"E{row_num}:J{row_num}",
            "values": [row_data]
        }

    # Execute concurrent requests using ThreadPoolExecutor
    updates = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scrape_single_url, task): task for task in current_batch_tasks}
        
        for future in as_completed(futures):
            try:
                row_num, update_payload = future.result()
                updates.append(update_payload)
            except Exception as e:
                print(f"Thread execution error: {e}")

    # Batch write back to Google Sheets (Columns E through J)
    if updates:
        sheet.batch_update(updates)
        print(f"\nSuccessfully batch-updated {len(updates)} records to Google Sheets!")

    # Auto-trigger next run if there are still remaining unscraped URLs
    remaining_tasks = tasks[MAX_ROWS_PER_RUN:]
    if remaining_tasks:
        print(f"{len(remaining_tasks)} URLs remaining. Triggering next GitHub workflow run...")
        trigger_next_run()
    else:
        print("All target URLs successfully processed!")

def extract_agent_data(session, url):
    try:
        response = session.get(url, timeout=15)
        if response.status_code != 200:
            return ["", "", "", "", "", f"Error: HTTP {response.status_code}"]

        match = re.search(r'(\{"@context":"https://schema\.org","@type":"RealEstateAgent".*?\})', response.text)
        if match:
            json_str = match.group(1).replace(r'\"', '"')
            data = json.loads(json_str)
            description = data.get("description", "")
            agency = description.split("- ")[-1].split(".")[0].strip() if "-" in description else ""

            return [
                data.get("name", ""),
                agency,
                data.get("telephone", ""),
                data.get("email", ""),
                data.get("image", ""),
                "Scraped"
            ]

        soup = BeautifulSoup(response.text, "html.parser")
        h1 = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else ""
        return [name, "", "", "", "", "Scraped (HTML Fallback)"]

    except Exception as e:
        return ["", "", "", "", "", f"Error: {str(e)}"]

def trigger_next_run():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    
    if token and repo:
        api_url = f"https://api.github.com/repos/{repo}/dispatches"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        data = {"event_type": "continue_scraping"}
        requests.post(api_url, headers=headers, json=data)

if __name__ == "__main__":
    main()

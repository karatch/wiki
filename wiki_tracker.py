import requests
from bs4 import BeautifulSoup
import time, json, re, smtplib
from email.mime.text import MIMEText
from email.header import Header
import unicodedata


WIKI_URL = "https://en.wikipedia.org/wiki/Deaths_in_August_2023"
# WIKI_URL = "https://en.wikipedia.org/wiki/Deaths_in_December_2025"

# интервал проверки (в секундах)
CHECK_INTERVAL_SECONDS = 60 * 5

# настройки электронной почты
SMTP_SERVER = 'smtp.yandex.ru'
SMTP_PORT = 587 # или 465
SMTP_USERNAME = "your_sender_email@yandex.ru"
SMTP_PASSWORD = "your_email_password"
RECEIVER_EMAIL = "recipient_email@yandex.ru"

# файл json для хранения обработанных данных
STATE_FILE = WIKI_URL.split('/')[-1].lower() + '.json'


def load_processed_deaths():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def save_processed_deaths(deaths_set):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(deaths_set), f, ensure_ascii=False, indent=4)


def find_russian_wiki_link(en_page_title):
    api_url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "titles": en_page_title,
        "prop": "langlinks",
        "lllimit": 500,
        "lllang": "ru"
    }

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0'
        }
        response = requests.get(api_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        pages = data.get("query", {}).get("pages", {})
        for page_id in pages:
            langlinks = pages[page_id].get("langlinks", [])
            for link in langlinks:
                if link.get("lang") == "ru":
                    return f"https://ru.wikipedia.org/wiki/{link['*']}"
        return None
    except requests.RequestException as e:
        print(f"Ошибка при запросе API Википедии для {en_page_title}: {e}")
        return None


def get_summary_and_title(wiki_url):
    page_title = ""
    summary = "Не удалось получить краткое описание."

    try:
        #  название статьи из URL (независимо ru или en)
        match = re.search(r'/wiki/([^/]+)$', wiki_url)
        if match:
            # URL-декодирование и замена символов
            page_title_encoded = match.group(1)
            # избавляемся от %-кодирования, чтобы использовать его в запросе API
            page_title = requests.utils.unquote(page_title_encoded).replace('_', ' ')

        #  API для получения первого абзаца (вместо скрапинга)
        api_url = "https://ru.wikipedia.org/w/api.php" if "ru.wikipedia" in wiki_url else "https://en.wikipedia.org/w/api.php"

        params = {
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "titles": page_title,
            "exintro": 1,  # только вводная часть
            "explaintext": 1,  # только текст, без HTML/Wiki-разметки
            "redirects": 1,  # следовать редиректам
            "exchars": 500  # ограничить количество символов
        }

        headers = {
            'User-Agent': 'Mozilla/5.0'
        }
        response = requests.get(api_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        pages = data.get("query", {}).get("pages", {})
        for page_id in pages:
            extract = pages[page_id].get("extract", "")
            summary = re.sub(r'\s+', ' ', extract).strip()
            summary = re.sub(r'\[.*?\]', '', summary).strip()

            # обновляем заголовок на точное название из API, если доступно
            api_title = pages[page_id].get("title", "")
            if api_title:
                page_title = api_title

            return page_title, summary.split('\n')[0]  # только первый абзац

    except requests.RequestException as e:
        print(f"Ошибка при получении сводки для {wiki_url}: {e}")

    return page_title, summary


def send_email(subject, body):
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = SMTP_USERNAME
    msg['To'] = RECEIVER_EMAIL

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, RECEIVER_EMAIL, msg.as_string())
        print(f"Успешно отправлено письмо: '{subject}'")
    except Exception as e:
        print(f"Ошибка при отправке письма: {e}")


def get_new_deaths(url, processed_deaths):
    print(f"\nПроверка страницы: {url}")
    new_deaths = []

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0'
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # основной контейнер со списком.
        content = soup.find('div', id='bodyContent')
        if not content:
            content = soup.find('div', id='content')

        if not content:
            print("Не удалось найти основной контент страницы.")
            return new_deaths

        # элементы <li> в разделах, представляющих даты
        for ul in content.find_all('ul'):
            for li in ul.find_all('li', recursive=False):
                # первый тег <a> в <li>
                link = li.find('a', href=True)
                if not link:
                    continue

                cite_ref = li.find('sup')
                if not cite_ref:
                    continue

                # фильтр: пропускаем ссылки, которые ведут не на статью, а на другую часть страницы
                if link['href'].startswith(('#', '/wiki/Category:', '/wiki/File:')):
                    continue

                # full_name = li.get_text().split(' – ')[0].strip()
                full_name = li.get_text().split('[')[0].strip()
                en_title = link['href'].split('/wiki/')[-1]
                en_url = f"https://en.wikipedia.org{link['href']}"

                # уникальный ключ для отслеживания
                death_key = f"{full_name} ({en_title})"

                if death_key not in processed_deaths:
                    new_deaths.append({
                        'name': full_name,
                        'en_title': en_title,
                        'en_url': en_url,
                        'key': death_key
                    })
                    print(f"   Найдена новая запись: {full_name}")

        return new_deaths

    except requests.RequestException as e:
        print(f"Ошибка при доступе к странице Википедии: {e}")
        return new_deaths


def main_loop():
    processed_deaths = load_processed_deaths()
    print(f"Загружено {len(processed_deaths)} ранее обработанных записей.")

    while True:
        new_deaths = get_new_deaths(WIKI_URL, processed_deaths)
        if new_deaths:
            for item in new_deaths:
                name = item['name']
                en_title = item['en_title']
                en_url = item['en_url']
                key = item['key']

                print(f"   Обработка: {name}")

                # ссылки на русскую Википедию
                ru_url = find_russian_wiki_link(en_title)
                final_url = ru_url if ru_url else en_url

                title, summary = get_summary_and_title(final_url)

                summary = unicodedata.normalize('NFD', summary)
                only_chars = [
                    char for char in summary
                    if unicodedata.category(char) != 'Mn'
                ]
                summary = ''.join(only_chars)

                email_body = (
                    f"Появилась новая запись на странице списка смертей:\n\n"
                    f"Имя: {title if title else name}\n"
                    f"Ссылка на статью: {final_url}\n\n"
                    f"Краткое описание (первый абзац):\n"
                    f"----------------------------------------\n"
                    f"{summary}\n"
                    f"----------------------------------------"
                )

                send_email(f"Новая запись о смерти: {title if title else name}", email_body)

                processed_deaths.add(key)

            save_processed_deaths(processed_deaths)
        else:
            print("Новых записей не найдено.")

        print(f"Следующая проверка через {CHECK_INTERVAL_SECONDS} секунд.")
        time.sleep(CHECK_INTERVAL_SECONDS)

# Для запуска скрипта используйте nohup python3 wiki_tracker.py &
if __name__ == "__main__":
    print("--- Скрипт Wiki Tracker запущен ---")
    print(f"Отслеживаемый URL: {WIKI_URL}")
    print(f"Интервал проверки: {CHECK_INTERVAL_SECONDS} сек.")
    main_loop()
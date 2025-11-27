# install_playwright.py
import asyncio
from playwright.async_api import async_playwright

async def install_browsers():
    async with async_playwright() as p:
        print("Устанавливаем Chromium...")
        await p.chromium.install()
        print("Устанавливаем Firefox...")
        await p.firefox.install()
        print("Устанавливаем WebKit...")
        await p.webkit.install()
    print("Все браузеры установлены!")

if __name__ == "__main__":
    asyncio.run(install_browsers())

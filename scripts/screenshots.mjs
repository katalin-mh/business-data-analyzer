// Captures README screenshots from a running app (streamlit run app.py).
// Needs Node with Playwright installed where Node can find it (npm i playwright).
// Usage: node scripts/screenshots.mjs [output-dir]
import { chromium } from "playwright";

const url = process.env.APP_URL ?? "http://localhost:8501";
const out = (process.argv[2] ?? "docs/screenshots").replace(/\/?$/, "/");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 1 });
await page.goto(url);
await page.getByRole("button", { name: "Use sample data (synthetic)" }).click();
await page.getByText("Average order value").waitFor();
await page.waitForTimeout(1500); // let the charts finish drawing
await page.screenshot({ path: `${out}overview.png` });

await page.getByRole("tab", { name: "Breakdown" }).click();
await page.waitForTimeout(1500);
await page.screenshot({ path: `${out}breakdown.png` });

await page.getByRole("tab", { name: "Data quality" }).click();
await page.waitForTimeout(800);
await page.screenshot({ path: `${out}data-quality.png` });

await page.getByRole("tab", { name: "SQL" }).click();
const box = page.getByRole("textbox", { name: "SQL" });
await box.fill(
  "SELECT region, ROUND(SUM(amount), 2) AS revenue, COUNT(*) AS orders\nFROM sales GROUP BY region ORDER BY revenue DESC",
);
await page.keyboard.press("Control+Enter");
await page.getByRole("button", { name: "Run query" }).click();
await page.waitForTimeout(1500);
await page.screenshot({ path: `${out}sql.png` });

await page.getByRole("tab", { name: "Export" }).click();
await page.getByRole("button", { name: "Prepare downloads" }).click();
await page.getByRole("button", { name: "Excel workbook" }).waitFor({ timeout: 60_000 });
await page.screenshot({ path: `${out}export.png` });

await browser.close();
console.log(`Screenshots written to ${out}`);

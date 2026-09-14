import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const consoleErrors = [];
page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text()); });
page.on("pageerror", (e) => consoleErrors.push("[pageerror] " + e.message));

await page.goto("http://localhost:3000", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2000);

await page.getByRole("button", { name: "Draw region" }).click();
await page.waitForTimeout(300);
const box = await page.locator('[data-testid="map-container"]').boundingBox();
const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
const t0 = Date.now();
await page.mouse.move(cx - 100, cy - 100);
await page.mouse.down();
await page.mouse.move(cx + 100, cy + 100, { steps: 15 });
await page.mouse.up();
await page.waitForResponse((r) => r.url().includes("/region/analyze"), { timeout: 120000 });
console.log("analyze (via UI) took ms:", Date.now() - t0);
await page.waitForTimeout(1000);

await page.getByRole("button", { name: "Cool Roof" }).click();
const simResp = await page.waitForResponse((r) => r.url().includes("/simulate"), { timeout: 60000 });
console.log("simulate status:", simResp.status());
await page.waitForTimeout(1000);
await page.screenshot({ path: "D:/Projects/UC/frontend/scripts/final_check.png" });

console.log("console errors:", consoleErrors.filter(e => !e.includes("GPU stall")));
await browser.close();

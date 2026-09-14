import { chromium } from "playwright";

const consoleErrors = [];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});
page.on("pageerror", (err) => consoleErrors.push("pageerror: " + err.message));
page.on("requestfailed", (req) => console.log("REQUEST FAILED:", req.url(), req.failure()?.errorText));
page.on("response", async (res) => {
  if (res.url().includes("/api/v1/")) {
    console.log("API RESPONSE:", res.status(), res.url());
  }
});

console.log("Navigating to http://localhost:3000 ...");
await page.goto("http://localhost:3000", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForSelector("text=Urban Cool", { timeout: 15000 });
console.log("Page loaded, title text found.");

await page.waitForTimeout(2000); // let map tiles start loading
await page.screenshot({ path: "_smoke_1_initial.png" });
console.log("Screenshot 1 (initial) saved.");

const debugInfo = await page.evaluate(() => {
  const el = document.querySelector('[data-testid="map-container"]');
  if (!el) return { found: false };
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  return {
    found: true,
    rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
    computedHeight: style.height,
    computedPosition: style.position,
    parentClass: el.parentElement ? el.parentElement.className : null,
    parentRect: el.parentElement ? el.parentElement.getBoundingClientRect() : null,
    hasWebGL: (() => {
      try {
        const c = document.createElement("canvas");
        return !!(c.getContext("webgl2") || c.getContext("webgl"));
      } catch {
        return false;
      }
    })(),
    childCount: el.childElementCount,
    innerHTML: el.innerHTML.slice(0, 300),
  };
});
console.log("DEBUG map container info:", JSON.stringify(debugInfo, null, 2));

console.log("Clicking Draw region...");
await page.click("text=Draw region");
await page.waitForTimeout(300);

const mapBox = await page.getByTestId("map-container").boundingBox();
if (!mapBox) throw new Error("Could not find map container bounding box");
console.log("Map container bounding box:", JSON.stringify(mapBox));

const startX = mapBox.x + mapBox.width / 2 - 60;
const startY = mapBox.y + mapBox.height / 2 - 60;
const endX = mapBox.x + mapBox.width / 2 + 60;
const endY = mapBox.y + mapBox.height / 2 + 60;

console.log(`Drawing a box from (${startX},${startY}) to (${endX},${endY}) ...`);
const analyzeResponsePromise = page.waitForResponse(
  (res) => res.url().includes("/api/v1/region/analyze") && res.request().method() === "POST",
  { timeout: 180000 },
);
await page.mouse.move(startX, startY);
await page.mouse.down();
await page.mouse.move((startX + endX) / 2, (startY + endY) / 2, { steps: 5 });
await page.mouse.move(endX, endY, { steps: 5 });
await page.mouse.up();

console.log("Waiting for the REAL /api/v1/region/analyze network response (this is genuinely slow, SHAP-heavy -- 1-3 min)...");
try {
  const response = await analyzeResponsePromise;
  console.log("Analyze response status:", response.status());
  await page.waitForSelector("text=main drivers", { timeout: 15000 });
  console.log("Explanation panel shows the real completed analysis.");
} catch (e) {
  console.log("TIMEOUT/ERROR waiting for analyze response:", e.message);
}

await page.screenshot({ path: "_smoke_2_after_draw.png" });
console.log("Screenshot 2 (after draw) saved.");

const bodyTextAfterDraw = await page.locator("body").innerText();
console.log("--- Body text snippet after draw ---");
console.log(bodyTextAfterDraw.slice(0, 800));

console.log("Clicking 'Add Tree Cover' intervention...");
await page.getByRole("button", { name: "Add Tree Cover" }).click();
await page.waitForTimeout(1000);
console.log("Simulating text visible?", await page.locator("text=Simulating...").isVisible().catch(() => false));

console.log("Waiting for intervention result table...");
try {
  await page.waitForSelector("text=Top locations", { timeout: 90000 });
  console.log("Ranked table appeared.");
} catch (e) {
  console.log("TIMEOUT waiting for ranked table:", e.message);
}

await page.screenshot({ path: "_smoke_3_after_intervention.png" });
console.log("Screenshot 3 (after intervention) saved.");

const bodyTextAfterIntervention = await page.locator("body").innerText();
console.log("--- Body text snippet after intervention ---");
console.log(bodyTextAfterIntervention.slice(0, 1200));

console.log("--- Console errors captured ---");
if (consoleErrors.length === 0) {
  console.log("NONE");
} else {
  consoleErrors.forEach((e) => console.log("ERROR:", e));
}

await browser.close();
console.log("DONE");

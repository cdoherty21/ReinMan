import { test, expect } from "@playwright/test";

// Synthetic fixture only. Real racing records are not touched by these UI tests.
const horse = {
  horse_id: "fixture", horse_name: "Example Runner", trainer_name: "Example Trainer",
  identity: { age_sex: "4/G" }, favorite: 0,
  record: { starts: 8, wins: 2, places: 1, shows: 1, recent: [1, 2, 3],
    classification: "Rising Star", confidence: "Moderate", reason: "Recorded improvement", weakness: "Limited samples" },
  news: [], history: [], preferences: { "Surface · D": { starts: 4, itm: 3 } },
};
for (const width of [390, 768, 1280]) {
  test(`directory and profile fit ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      let data: unknown = { horses: [horse], after: null };
      if (path === "/api/session") data = { authenticated: true, authRequired: true };
      else if (path.endsWith("/featured")) data = [{...horse, recent_form: "[1,2,3]"}];
      else if (path.endsWith("/counts")) data = { indexed: 1, archive_keys: 0, enriched: 0, photographs: 0 };
      else if (path.endsWith("/notifications")) data = [];
      else if (path.endsWith("/horse/fixture")) data = horse;
      else if (path.endsWith("/compare")) data = { horses: [horse], headToHead: [] };
      await route.fulfill({ json: data });
    });
    await page.goto("/stables");
    await expect(page.getByRole("heading", {name:"The Stables",exact:true})).toBeVisible();
    await expect(page.getByRole("heading", {name:"Horse directory"})).toBeVisible();
    await expect(page.getByRole("link", {name:"Example Runner"}).first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.getByRole("button", {name:"Compare",exact:true}).click();
    await expect(page.getByRole("heading", {name:"Head-to-head history"})).toBeVisible();
    await page.getByRole("button", {name:"Clear comparison"}).click();
    await page.getByRole("link", {name:"View profile →"}).first().click();
    await expect(page.getByRole("heading", {name:"Example Runner",exact:true})).toBeVisible();
    await expect(page.getByRole("heading", {name:"Rein Man’s Assessment"})).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}

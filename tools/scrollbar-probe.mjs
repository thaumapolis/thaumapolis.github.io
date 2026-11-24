#!/usr/bin/env node
/**
 * Report how much layout width a scrollbar takes in this rendering environment.
 *
 * Goldens are full-page screenshots, so a classic scrollbar shrinks clientWidth
 * and re-wraps every paragraph, while an overlay scrollbar does not. That single
 * number decides whether a runner and a container can ever paint the same page
 * at the same height, and nothing else in the fingerprint records it.
 */
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const entry = process.env.PLAYWRIGHT_ENTRY;
if (!entry) {
  console.error('PLAYWRIGHT_ENTRY is unset; cannot measure the rendering environment');
  process.exit(1);
}
const playwright = require(entry);
const browser = await playwright.firefox.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await context.newPage();
await page.setContent('<body style="height:4000px">tall</body>');
const measured = await page.evaluate(() => {
  const probe = document.createElement('div');
  probe.style.cssText = 'width:100px;height:100px;overflow:scroll;position:absolute;visibility:hidden';
  document.body.appendChild(probe);
  const scrollbar = probe.offsetWidth - probe.clientWidth;
  probe.remove();
  return {
    scrollbar,
    innerWidth: window.innerWidth,
    clientWidth: document.documentElement.clientWidth,
    tallPageScrollHeight: document.documentElement.scrollHeight,
  };
});
await browser.close();
console.log('scrollbar probe: ' + JSON.stringify(measured));

const { chromium } = require('playwright');
const path = require('path'); const { pathToFileURL } = require('url');
const ROOT = 'D:/desktop/电量显示插件';
const PAGE = pathToFileURL(path.join(ROOT, 'design/motion/motion-lab.html')).href;
(async () => {
  for (const mode of ['no-preference', 'reduce']) {
    const b = await chromium.launch();
    const p = await b.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: mode });
    await p.goto(PAGE, { waitUntil: 'load' });
    await p.waitForTimeout(1500);
    const info = await p.evaluate(() => {
      const probe = (sel) => {
        const n = document.querySelector(sel);
        if (!n) return null;
        const cs = getComputedStyle(n);
        const anims = n.getAnimations();
        return { dur: cs.animationDuration, name: cs.animationName, play: cs.animationPlayState, anims: anims.length, running: anims.filter(a => a.playState === 'running').length };
      };
      const stageBefore = getComputedStyle(document.querySelector('.stage'), '::before');
      return {
        mq: matchMedia('(prefers-reduced-motion: reduce)').matches,
        total: document.getAnimations().length,
        running: document.getAnimations().filter(a => a.playState === 'running').length,
        ambient: { dur: stageBefore.animationDuration, name: stageBefore.animationName, opacity: stageBefore.opacity },
        ringGlow: probe('.breath-normal .glow'),
        shine: probe('.ring-lg .shine'),
        liveDot: probe('.live i'),
        introClass: document.body.className
      };
    });
    console.log(mode, JSON.stringify(info));
    await p.screenshot({ path: path.join(ROOT, 'design/verify/motion/diag-' + mode + '.png') });
    await b.close();
  }
})();

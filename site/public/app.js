// The gallery page: landing slideshow, TV mode, people filter, chapters and the lightbox.
// Data comes from /data/gallery.json, written by pipeline/publish.py.
import PhotoSwipeLightbox from '/vendor/photoswipe-lightbox.esm.min.js';

const $ = (s) => document.querySelector(s);
const thumb = (p) => `/img/thumb/${p.id}.jpg`;
const web = (p) => `/img/web/${p.id}.jpg`;
const avatar = (person) => `/img/avatar/f${person.face}.jpg`;
const fmt = (t) => { const [h, m] = t.split(':').map(Number); return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`; };
const shuffle = (a) => { a = [...a]; for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };

const res = await fetch('/data/gallery.json');
if (res.status === 401) location.reload();
const raw = await res.json();

// photos: [id, width, height, "HH:MM", "#colour"] in the order they were taken
const photos = raw.photos.map(([id, width, height, time, color], index) => ({ id, width, height, time, color, index }));
const chapters = raw.chapters.map((c, n) => {
  const end = raw.chapters[n + 1]?.start ?? photos.length;
  return { ...c, n, photos: photos.slice(c.start, end) };
});
for (const c of chapters) for (const p of c.photos) p.chapter = c;
const people = raw.people.map((p, n) => ({ ...p, n, set: new Set(p.photos) }));
const highlights = raw.highlights.map((i) => photos[i]);

document.title = raw.title;
$('#title').textContent = raw.title;
$('#where').textContent = raw.where ?? '';
$('#mono').textContent = raw.monogram;
$('#enter').textContent = `View all ${photos.length} photos`;

// ---- slideshow (landing and TV) ----

function slideshow(host, { seconds = 6, onShow } = {}) {
  const order = [...shuffle(highlights), ...shuffle(photos)];
  let n = 0;
  let front = null;
  let timer = null;
  const next = () => {
    const p = order[n++ % order.length];
    const img = new Image();
    img.alt = '';
    img.onload = () => {
      const slide = document.createElement('div');
      slide.className = 'slide';
      slide.style.animationDuration = `${seconds + 3}s`;
      // fill the screen when photo and screen are the same way round; otherwise show it whole
      // over a blurred copy of itself, so there are no black bars
      const fits = (p.width > p.height) === (host.clientWidth > host.clientHeight);
      img.style.objectFit = fits ? 'cover' : 'contain';
      if (!fits) {
        const backdrop = img.cloneNode();
        backdrop.className = 'backdrop';
        backdrop.style.objectFit = 'cover';
        slide.append(backdrop);
      }
      slide.append(img);
      host.append(slide);
      requestAnimationFrame(() => requestAnimationFrame(() => { slide.style.opacity = 1; }));
      const old = front;
      front = slide;
      setTimeout(() => old?.remove(), 1800);
      onShow?.(p);
    };
    img.src = web(p);
  };
  return {
    start() { if (!timer) { next(); timer = setInterval(next, seconds * 1000); } },
    stop() { clearInterval(timer); timer = null; },
    next() { this.stop(); this.start(); },
    clear() { this.stop(); host.querySelectorAll('.slide').forEach((i) => i.remove()); front = null; },
  };
}

const heroShow = slideshow($('#show'), { onShow: (p) => { $('#now').textContent = `${p.chapter.title} · ${fmt(p.time)}`; } });
// only run the landing slideshow while it's on screen
new IntersectionObserver(([e]) => (e.isIntersecting ? heroShow.start() : heroShow.stop())).observe($('#hero'));
$('#enter').onclick = () => $('#bar').scrollIntoView({ behavior: 'smooth' });

// TV mode: full screen, no controls, screen kept awake. /tv opens straight into it.
const tv = $('#tv');
const tvShow = slideshow(tv, { seconds: 8, onShow: (p) => { $('#tv-cap').textContent = p.chapter.title; } });
let wakeLock = null;
const keepAwake = async () => { try { wakeLock = await navigator.wakeLock?.request('screen'); } catch {} };
async function startTv() {
  tv.hidden = false;
  tv.classList.add('playing');
  heroShow.stop();
  await tv.requestFullscreen?.().catch(() => {});
  keepAwake();
  tvShow.start();
}
function stopTv() {
  tvShow.clear();
  tv.classList.remove('playing');
  tv.hidden = true;
  wakeLock?.release().catch(() => {});
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  if (location.pathname === '/tv') history.replaceState(null, '', '/');
}
$('#tv-btn').onclick = startTv;
$('#tv-start').onclick = startTv;
tv.addEventListener('click', (e) => { if (tv.classList.contains('playing') && e.target !== $('#tv-start')) stopTv(); });
document.addEventListener('keydown', (e) => {
  if (tv.hidden) return;
  if (e.key === 'Escape' || e.key === 'Backspace' || e.key === 'GoBack') stopTv();
  if (e.key === 'ArrowRight') tvShow.next();
});
document.addEventListener('visibilitychange', () => { if (!tv.hidden && document.visibilityState === 'visible') keepAwake(); });
document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && tv.classList.contains('playing')) stopTv(); });
if (location.pathname === '/tv') { tv.hidden = false; $('#tv-start').focus(); } // a button press is needed before going full screen

// ---- lightbox ----

let current = photos; // what the lightbox steps through: everything, or one person's photos
const lightbox = new PhotoSwipeLightbox({
  pswpModule: () => import('/vendor/photoswipe.esm.min.js'),
  bgOpacity: 0.97,
  showHideAnimationType: 'fade',
  preload: [1, 2],
  wheelToZoom: true,
});
lightbox.on('uiRegister', () => {
  lightbox.pswp.ui.registerElement({
    name: 'download',
    order: 8,
    isButton: true,
    tagName: 'a',
    title: 'Download the full-size photo',
    html: { isCustomSVG: true, inner: '<path d="M16 7v13m0 0-5-5m5 5 5-5M8 25h16" id="pswp__icn-download" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>', outlineID: 'pswp__icn-download' },
    onInit: (el, pswp) => {
      el.setAttribute('download', '');
      pswp.on('change', () => { el.href = `/download/${pswp.currSlide.data.id}`; });
    },
  });
});
lightbox.init();
function open(photo) {
  const list = current;
  lightbox.loadAndOpen(list.indexOf(photo), list.map((p) => {
    const scale = 2048 / Math.max(p.width, p.height);
    return { id: p.id, src: web(p), msrc: thumb(p), width: Math.round(p.width * scale), height: Math.round(p.height * scale), alt: `${p.chapter.title}, ${fmt(p.time)}` };
  }));
}

// ---- justified grid: every row fills the width, heights near the target ----

function justify(container, list) {
  const width = container.clientWidth;
  const small = width < 600;
  const target = small ? 150 : 240;
  const gap = small ? 4 : 6;
  container.textContent = '';
  let row = [];
  let ratio = 0;
  const flush = (last) => {
    const height = Math.min(last ? target : Infinity, (width - gap * (row.length - 1)) / ratio);
    const el = document.createElement('div');
    el.className = 'row';
    el.style.height = `${height}px`;
    for (const p of row) {
      const a = document.createElement('a');
      a.href = web(p);
      a.style.width = `${(p.width / p.height) * height}px`;
      a.style.background = p.color;
      a.onclick = (e) => { e.preventDefault(); open(p); };
      const img = new Image();
      img.loading = 'lazy';
      img.decoding = 'async';
      img.alt = `${p.chapter.title}, ${fmt(p.time)}`;
      img.onload = () => img.classList.add('loaded');
      img.src = thumb(p);
      a.append(img);
      el.append(a);
    }
    container.append(el);
    row = [];
    ratio = 0;
  };
  for (const p of list) {
    row.push(p);
    ratio += p.width / p.height;
    if ((width - gap * (row.length - 1)) / ratio < target) flush(false);
  }
  if (row.length) flush(true);
}

// ---- chapters, timeline rail and people filter ----

$('#chapters').innerHTML = chapters.map((c) => `<section class="chapter" id="ch${c.n}"><header><h2 class="serif">${c.title}</h2><span class="meta"></span></header><div class="grid"></div></section>`).join('');
$('#chapter-links').innerHTML = chapters.map((c) => `<a href="#ch${c.n}" data-n="${c.n}">${c.title}</a>`).join('');
$('#rail').innerHTML = chapters.map((c) => `<a href="#ch${c.n}" data-n="${c.n}"><b>${fmt(c.photos[0].time)}</b>${c.title}</a>`).join('');

let person = null;
function render() {
  current = person ? photos.filter((p) => person.set.has(p.index)) : photos;
  for (const c of chapters) {
    const list = person ? c.photos.filter((p) => person.set.has(p.index)) : c.photos;
    const section = $(`#ch${c.n}`);
    section.hidden = !list.length;
    section.querySelector('.meta').textContent = `${fmt(c.photos[0].time)} · ${list.length} photo${list.length === 1 ? '' : 's'}`;
    if (list.length) justify(section.querySelector('.grid'), list);
    else section.querySelector('.grid').textContent = '';
  }
}

$('#people').innerHTML = `<button class="on" data-n="" title="Everyone"><span class="all"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.6-3.2 2.8-5 5.5-5s4.9 1.8 5.5 5"/><circle cx="17" cy="9" r="2.4"/><path d="M15.5 14.2c2.3.1 4 1.6 4.5 4.3"/></svg></span>Everyone</button>` +
  people.map((p) => `<button data-n="${p.n}" title="${p.photos.length} photos"><img loading="lazy" alt="" src="${avatar(p)}"></button>`).join('');
$('#people').hidden = !people.length; // no face groups published
$('#people').onclick = (e) => {
  const button = e.target.closest('button');
  if (!button) return;
  person = button.dataset.n === '' ? null : people[Number(button.dataset.n)];
  $('#people').querySelectorAll('button').forEach((b) => b.classList.toggle('on', b === button));
  document.body.classList.toggle('filtered', !!person);
  if (person) {
    $('#note').innerHTML = `<img alt="" src="${avatar(person)}"> In ${person.photos.length} photos · <button>Show everyone</button>`;
    $('#note button').onclick = () => $('#people button').click();
  }
  render();
  scrollTo({ top: $('#chapters').offsetTop - $('#bar').offsetHeight + 10, behavior: 'smooth' });
};

render();
let resizeTimer;
let lastWidth = innerWidth;
addEventListener('resize', () => {
  if (innerWidth === lastWidth) return; // phones fire resize when the address bar hides
  lastWidth = innerWidth;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(render, 150);
});

// highlight the chapter being looked at
const spy = new IntersectionObserver((entries) => entries.forEach((e) => {
  if (!e.isIntersecting) return;
  const n = e.target.id.slice(2);
  document.querySelectorAll('.chapters a, .rail a').forEach((a) => a.classList.toggle('on', a.dataset.n === n));
}), { rootMargin: '-40% 0px -55% 0px' });
document.querySelectorAll('.chapter').forEach((s) => spy.observe(s));

// share: the link only; people still need the passcode
$('#share').onclick = async () => {
  const url = location.origin;
  if (navigator.share) return navigator.share({ title: raw.title, url }).catch(() => {});
  await navigator.clipboard?.writeText(url);
  const toast = $('#toast');
  toast.textContent = 'Link copied. Send the passcode separately.';
  toast.classList.add('on');
  setTimeout(() => toast.classList.remove('on'), 2600);
};

// US Drought Monitor Clone — shared interactivity

document.addEventListener('DOMContentLoaded', () => {

  // Dropdowns (Bootstrap-style)
  document.querySelectorAll('.btn-group > .dropdown').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const group = btn.parentElement;
      const wasOpen = group.classList.contains('open');
      document.querySelectorAll('.btn-group.open').forEach(g => g.classList.remove('open'));
      if (!wasOpen) group.classList.add('open');
    });
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.btn-group')) {
      document.querySelectorAll('.btn-group.open').forEach(g => g.classList.remove('open'));
    }
  });

  // Grayscale toggle (Current Map)
  const grayChk = document.getElementById('gray-chk');
  if (grayChk) {
    grayChk.addEventListener('change', () => {
      document.querySelectorAll('.map-img').forEach(img => {
        img.classList.toggle('grayscale', grayChk.checked);
      });
      document.querySelectorAll('.legend-swatch').forEach(sw => {
        sw.style.filter = grayChk.checked ? 'grayscale(1)' : '';
      });
    });
  }

  // Comparison Slider
  const slider = document.querySelector('.slider-stage');
  if (slider) {
    const inner = slider.querySelector('.slider-inner');
    const clip = slider.querySelector('.clip');
    const handle = slider.querySelector('.slider-handle');
    const clipImg = clip.querySelector('img');

    const syncSize = () => {
      const w = inner.getBoundingClientRect().width;
      if (clipImg) clipImg.style.width = w + 'px';
    };
    syncSize();
    window.addEventListener('resize', syncSize);

    let dragging = false;
    const update = (clientX) => {
      const rect = inner.getBoundingClientRect();
      let pct = ((clientX - rect.left) / rect.width) * 100;
      pct = Math.max(2, Math.min(98, pct));
      clip.style.width = pct + '%';
      handle.style.left = pct + '%';
    };
    handle.addEventListener('mousedown', () => dragging = true);
    handle.addEventListener('touchstart', () => dragging = true, { passive: true });
    window.addEventListener('mouseup', () => dragging = false);
    window.addEventListener('touchend', () => dragging = false);
    window.addEventListener('mousemove', (e) => { if (dragging) update(e.clientX); });
    window.addEventListener('touchmove', (e) => { if (dragging && e.touches[0]) update(e.touches[0].clientX); }, { passive: true });
    inner.addEventListener('click', (e) => update(e.clientX));
  }

  // Animation player
  const animStage = document.getElementById('anim-stage');
  if (animStage) {
    const frames = JSON.parse(animStage.dataset.frames);
    const img = animStage.querySelector('img');
    const dateLabel = document.getElementById('anim-date');
    const slider = document.getElementById('anim-slider');
    const playBtn = document.getElementById('anim-play');
    let idx = 0;
    let playing = false;
    let interval = null;

    const show = (i) => {
      idx = i;
      img.src = frames[i].src;
      dateLabel.textContent = frames[i].date;
      slider.value = i;
    };
    const play = () => {
      playing = true;
      playBtn.textContent = '❚❚ Pause';
      interval = setInterval(() => {
        let n = (idx + 1) % frames.length;
        show(n);
      }, 900);
    };
    const pause = () => {
      playing = false;
      playBtn.textContent = '▶ Play';
      clearInterval(interval);
    };
    playBtn.addEventListener('click', () => playing ? pause() : play());
    slider.addEventListener('input', (e) => {
      pause();
      show(parseInt(e.target.value));
    });
    document.getElementById('anim-prev').addEventListener('click', () => {
      pause();
      show((idx - 1 + frames.length) % frames.length);
    });
    document.getElementById('anim-next').addEventListener('click', () => {
      pause();
      show((idx + 1) % frames.length);
    });
    show(frames.length - 1); // start at most recent
  }
});

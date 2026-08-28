const API_ROOT = '/viewer/api';
const feed = document.querySelector('#feed');
const launchParams = new URLSearchParams(location.search);
const template = document.querySelector('#postTemplate');
const gridTemplate = document.querySelector('#gridPostTemplate');
const stats = document.querySelector('#stats');
let filter = 'all', platform = 'all', offset = 0, total = 0, loading = false, ended = false;
let feedGeneration = 0;
let activeCard = null, toastTimer = null, infoIdleTimer = null, selectedAccount = null;
let accountView = 'grid', collectionPage = null;
let homeReturn = null, pendingPlaybackFocus = null;
let accountGridReturn = null, pendingGridFocus = null;
let accountList = [], accountListReturn = false;
let feedSeed = launchParams.get('seed') || sessionStorage.getItem('viewer-seed') || crypto.randomUUID();
sessionStorage.setItem('viewer-seed', feedSeed);
let playbackVolume = Math.max(0, Math.min(1, Number(localStorage.getItem('viewer-volume') ?? .8)));
let loopVideos = localStorage.getItem('viewer-loop') !== 'false';
let rightHoldTimer = null, rightHoldVideo = null, rightHoldActive = false;
let rightHoldWasPaused = false, rightHoldPreviousRate = 1;
let rightHoldTapAction = null;
let fullscreenRestore = null;

fetch('/api/settings').then(response => response.json()).then(settings => {
  if (Number.isFinite(settings.viewer_volume)) {
    playbackVolume = Math.max(0, Math.min(1, settings.viewer_volume / 100));
    document.querySelectorAll('video').forEach(video => { video.volume = playbackVolume; });
  }
  if (typeof settings.viewer_loop === 'boolean') {
    loopVideos = settings.viewer_loop;
    document.querySelectorAll('video').forEach(video => { video.loop = loopVideos; });
  }
  if (settings.viewer_theme === 'dark' || settings.viewer_theme === 'light') {
    document.documentElement.dataset.theme = settings.viewer_theme;
    applyNativeTheme(settings.viewer_theme);
  }
}).catch(() => {});

const api = async (url, options = {}) => {
  if (url.startsWith('/api/')) url = `${API_ROOT}${url.slice(4)}`;
  const response = await fetch(url, {headers: {'Content-Type': 'application/json'}, ...options});
  if (!response.ok) throw new Error((await response.text()) || response.statusText);
  return response.json();
};

function applyNativeTheme(theme) {
  window.pywebview?.api?.set_theme?.(theme).catch?.(() => {});
}

function toast(message) {
  const element = document.querySelector('#toast');
  element.textContent = message; element.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => element.classList.remove('show'), 1800);
}

async function refreshStats() {
  const value = await api('/api/stats');
  stats.textContent = `${value.total.toLocaleString()} posts  ·  ${value.viewed.toLocaleString()} viewed  ·  ${value.liked.toLocaleString()} liked  ·  ${value.saved.toLocaleString()} saved`;
}

function createPost(post, position = 0) {
  const card = template.content.firstElementChild.cloneNode(true);
  card.classList.add('info-idle');
  card.querySelector('.unseen-mark').classList.toggle('hidden', Boolean(post.state?.viewed));
  card.dataset.key = post.key; card.dataset.position = String(position);
  card.dataset.mediaPage = '0'; card.post = post;
  const avatar = card.querySelector('.account-avatar img');
  avatar.src = post.avatar; avatar.alt = post.account;
  card.querySelector('.post-account').textContent = `@${post.account}`;
  card.querySelector('.post-title').textContent = post.title;
  card.querySelector('.post-meta').textContent = [post.group, post.platform, post.date, post.post_id].filter(Boolean).join('  ·  ');
  const strip = card.querySelector('.media-strip');
  const dots = card.querySelector('.media-dots');
  const counter = card.querySelector('.media-count');
  post.media.forEach((item, index) => {
    const element = document.createElement(item.kind === 'video' ? 'video' : 'img');
    element.className = 'media-item'; element.src = item.url;
    if (item.kind === 'video') {
      element.loop = loopVideos; element.volume = playbackVolume; element.playsInline = true;
      element.preload = 'metadata'; element.controls = false;
      element.addEventListener('timeupdate', () => updateProgress(card));
      element.addEventListener('durationchange', () => updateProgress(card));
      element.addEventListener('loadedmetadata', () => updateOrientation(card));
    }
    else {
      element.alt = post.title;
      element.addEventListener('load', () => updateOrientation(card));
    }
    element.addEventListener('error', () => card.querySelector('.media-error').classList.remove('hidden'));
    strip.appendChild(element);
    if (post.media.length > 1) {
      const dot = document.createElement('span'); dot.className = `media-dot ${index === 0 ? 'active' : ''}`; dots.appendChild(dot);
    }
  });
  strip.addEventListener('scroll', () => {
    const page = Math.round(strip.scrollLeft / Math.max(strip.clientWidth, 1));
    if (card.dataset.mediaTarget === undefined) {
      card.dataset.mediaPage = String(page);
    }
    [...dots.children].forEach((dot, index) => dot.classList.toggle('active', index === page));
    if (post.media.length > 1) counter.textContent = `${page + 1} / ${post.media.length}`;
    updateOrientation(card);
    updateProgress(card);
  }, {passive: true});
  if (post.media.length > 1) {
    counter.textContent = `1 / ${post.media.length}`;
    card.querySelector('.media-prev').classList.remove('hidden');
    card.querySelector('.media-next').classList.remove('hidden');
  }
  updateButtons(card);
  if (post.media.some(item => item.kind === 'video')) {
    card.querySelector('.video-progress').classList.remove('hidden');
  }
  const progress = card.querySelector('.video-progress');
  const seekVideo = event => {
    const video = currentVideo(card);
    if (!video || !Number.isFinite(video.duration)) return;
    const box = progress.getBoundingClientRect();
    video.currentTime = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)) * video.duration;
  };
  progress.addEventListener('pointerdown', event => {
    event.stopPropagation(); event.preventDefault();
    progress.setPointerCapture(event.pointerId);
    progress.classList.add('dragging');
    seekVideo(event);
  });
  progress.addEventListener('pointermove', event => {
    if (!progress.classList.contains('dragging')) return;
    event.stopPropagation(); seekVideo(event);
  });
  const finishSeek = event => {
    if (progress.hasPointerCapture(event.pointerId)) {
      progress.releasePointerCapture(event.pointerId);
    }
    progress.classList.remove('dragging');
  };
  progress.addEventListener('pointerup', finishSeek);
  progress.addEventListener('pointercancel', finishSeek);
  progress.addEventListener('click', event => {
    event.stopPropagation(); seekVideo(event);
  });
  card.addEventListener('click', event => {
    const button = event.target.closest('[data-action]');
    if (button) handleAction(card, button.dataset.action);
    else if (event.target.tagName === 'VIDEO') togglePlayback(event.target);
  });
  card.querySelector('.media-stage').addEventListener('dblclick', event => {
    if (event.target.closest('[data-action], .video-progress')) return;
    event.preventDefault();
    toggleFullscreen(card);
  });
  observer.observe(card);
  return card;
}

function createGridPost(post, position) {
  const card = gridTemplate.content.firstElementChild.cloneNode(true);
  card.dataset.key = post.key;
  const preview = card.querySelector('.grid-preview');
  const first = post.media[0];
  if (!first) {
    preview.innerHTML = '<div class="deleted-post"><span>−</span><strong>Deleted</strong></div>';
    card.querySelector('.grid-badge').textContent = post.deletion?.status === 'error' ? 'Delete error' : 'Deleted';
    card.querySelector('.grid-reactions').innerHTML = '<span class="grid-reaction disliked" title="Disliked">−</span>';
    card.querySelector('.grid-caption strong').textContent = post.title;
    card.querySelector('.grid-caption small').textContent = post.date || post.post_id;
    card.disabled = true;
    return card;
  }
  const media = document.createElement(first.kind === 'video' ? 'video' : 'img');
  media.src = first.url;
  if (first.kind === 'video') {
    media.preload = 'metadata'; media.muted = true; media.playsInline = true;
  } else media.alt = post.title;
  preview.appendChild(media);
  if (post.media.length > 1) card.querySelector('.grid-badge').textContent = `${post.media.length} files`;
  else if (first.kind === 'video') card.querySelector('.grid-badge').textContent = 'Video';
  const reactions = card.querySelector('.grid-reactions');
  if (post.state.reaction === 'like') {
    reactions.insertAdjacentHTML('beforeend', '<span class="grid-reaction liked" title="Liked">♥</span>');
  }
  if (post.state.reaction === 'dislike') {
    reactions.insertAdjacentHTML('beforeend', '<span class="grid-reaction disliked" title="Disliked">−</span>');
  }
  if (post.state.saved) {
    reactions.insertAdjacentHTML('beforeend', '<span class="grid-reaction saved" title="Saved">★</span>');
  }
  card.querySelector('.grid-caption strong').textContent = post.title;
  card.querySelector('.grid-caption small').textContent = post.date || post.post_id;
  card.addEventListener('click', () => {
    accountGridReturn = {
      key: post.key, position, scrollTop: feed.scrollTop,
    };
    startAccountPlayback('newest', position, post.key);
  });
  return card;
}

function updateButtons(card) {
  const state = card.post.state;
  card.querySelector('[data-action="like"]').classList.toggle('active', state.reaction === 'like');
  card.querySelector('[data-action="dislike"]').classList.toggle('active', state.reaction === 'dislike');
  card.querySelector('[data-action="save"]').classList.toggle('active', state.saved);
}

async function saveState(card, patch) {
  Object.assign(card.post.state, patch);
  updateButtons(card);
  try {
    const saved = await api('/api/state', {method: 'POST', body: JSON.stringify({key: card.post.key, ...patch})});
    Object.assign(card.post.state, saved.state); updateButtons(card); refreshStats();
    if (saved.deletion?.status === 'pending') {
      toast('Post queued for deletion in 5 minutes · Dislike again to undo');
    } else if (saved.deletion?.status === 'cancelled') {
      toast('Scheduled deletion cancelled');
    }
  }
  catch (error) { toast(`Could not save: ${error.message}`); }
}

function handleAction(card, action) {
  const state = card.post.state;
  if (action === 'like') saveState(card, {reaction: state.reaction === 'like' ? null : 'like'});
  if (action === 'dislike') saveState(card, {reaction: state.reaction === 'dislike' ? null : 'dislike'});
  if (action === 'save') saveState(card, {saved: !state.saved});
  if (action === 'account') openAccount(card.post, card);
  if (action === 'previous-media') moveMedia(card, -1);
  if (action === 'next-media') moveMedia(card, 1);
  if (action === 'fullscreen') {
    toggleFullscreen(card);
  }
}

async function toggleFullscreen(card) {
  try {
    const strip = card.querySelector('.media-strip');
    const page = Math.round(strip.scrollLeft / Math.max(strip.clientWidth, 1));
    card.dataset.mediaPage = String(page);
    fullscreenRestore = {card, strip, page};

    if (window.parent !== window) {
      window.parent.postMessage({type: 'archiver:toggle-viewer-fullscreen'}, '*');
      return;
    }
    if (window.pywebview?.api?.toggle_fullscreen) {
      const fullscreen = await window.pywebview.api.toggle_fullscreen();
      applyMonitorFullscreen(Boolean(fullscreen));
      return;
    }
    const stage = card.querySelector('.media-stage');
    if (document.fullscreenElement) await document.exitFullscreen();
    else await stage.requestFullscreen();
  } catch (_) {
    toast('Fullscreen is unavailable');
  }
}

function applyMonitorFullscreen(fullscreen) {
  document.documentElement.classList.toggle('native-fullscreen', fullscreen);
  const restore = fullscreenRestore;
  if (!restore?.card?.isConnected) return;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    restore.strip.scrollLeft = restore.page * restore.strip.clientWidth;
    restore.card.dataset.mediaPage = String(restore.page);
    restore.card.scrollIntoView({behavior: 'auto', block: 'start'});
    activeCard = restore.card;
  }));
}

document.addEventListener('fullscreenchange', () => {
  document.documentElement.classList.toggle(
    'native-fullscreen', Boolean(document.fullscreenElement));
});

function togglePlayback(video) { video.paused ? video.play().catch(() => {}) : video.pause(); }

window.viewerPauseAll = () => {
  document.querySelectorAll('video').forEach(video => video.pause());
};

function moveMedia(card, direction) {
  const strip = card.querySelector('.media-strip');
  const measured = Math.round(strip.scrollLeft / Math.max(strip.clientWidth, 1));
  const stored = Number(card.dataset.mediaTarget ?? card.dataset.mediaPage);
  const current = Number.isInteger(stored) ? stored : measured;
  const target = Math.max(0, Math.min(strip.children.length - 1, current + direction));
  card.dataset.mediaPage = String(target);
  card.dataset.mediaTarget = String(target);
  strip.scrollTo({left: target * strip.clientWidth, behavior: 'smooth'});
  clearTimeout(card.mediaTargetTimer);
  card.mediaTargetTimer = setTimeout(() => {
    delete card.dataset.mediaTarget;
    card.dataset.mediaPage = String(
      Math.round(strip.scrollLeft / Math.max(strip.clientWidth, 1))
    );
  }, 450);
}

function currentVideo(card) {
  const strip = card.querySelector('.media-strip');
  const page = Math.round(strip.scrollLeft / Math.max(strip.clientWidth, 1));
  const item = strip.children[page];
  return item?.tagName === 'VIDEO' ? item : null;
}

function updateOrientation(card) {
  const strip = card.querySelector('.media-strip');
  const page = Math.round(strip.scrollLeft / Math.max(strip.clientWidth, 1));
  const item = strip.children[page];
  const width = item?.tagName === 'VIDEO' ? item.videoWidth : item?.naturalWidth;
  const height = item?.tagName === 'VIDEO' ? item.videoHeight : item?.naturalHeight;
  if (width && height) {
    card.dataset.orientation = width / height > 1.15 ? 'landscape' : 'portrait';
  }
}

function updateProgress(card) {
  const video = currentVideo(card), progress = card.querySelector('.video-progress');
  const fill = card.querySelector('.video-progress-fill');
  progress.classList.toggle('hidden', !video);
  const ratio = video && Number.isFinite(video.duration) && video.duration > 0
    ? Math.min(1, video.currentTime / video.duration) : 0;
  fill.style.width = `${ratio * Math.max(0, progress.clientWidth - 8)}px`;
}

function wakePostInfo() {
  if (!activeCard || isGridPage()) return;
  activeCard.classList.remove('info-idle');
  clearTimeout(infoIdleTimer);
  infoIdleTimer = setTimeout(() => {
    if (activeCard) activeCard.classList.add('info-idle');
  }, 1000);
}

function wakePostInfoNearTitle(event) {
  if (!activeCard || isGridPage()) return;
  const rect = activeCard.getBoundingClientRect();
  const hotspotHeight = 120;
  if (event.clientX >= rect.left && event.clientX <= rect.right
      && event.clientY >= rect.bottom - hotspotHeight && event.clientY <= rect.bottom) {
    wakePostInfo();
  }
}

function renderAccountList() {
  const query = document.querySelector('#accountsSearch').value.trim().toLocaleLowerCase();
  const items = accountList.filter(account =>
    !query || `${account.account} ${account.group} ${account.platform}`.toLocaleLowerCase().includes(query)
  );
  const grid = document.querySelector('#accountsGrid');
  grid.innerHTML = '';
  document.querySelector('#accountsMeta').textContent =
    `${items.length.toLocaleString()} of ${accountList.length.toLocaleString()} accounts`;
  if (!items.length) {
    grid.innerHTML = '<div class="accounts-empty">No accounts match this search.</div>';
    return;
  }
  items.forEach(account => {
    const card = document.createElement('button');
    card.className = 'account-list-card';
    card.innerHTML = '<img alt=""><div><strong></strong><small class="account-group"></small><small class="account-count"></small></div>';
    card.querySelector('img').src = account.avatar;
    card.querySelector('img').alt = account.account;
    card.querySelector('strong').textContent = account.account;
    card.querySelector('.account-group').textContent = account.group || 'Unassigned';
    card.querySelector('.account-count').textContent =
      `${account.platform} · ${account.posts.toLocaleString()} posts`;
    card.addEventListener('click', () => openAccount(account));
    grid.appendChild(card);
  });
}

async function showAccountsPage() {
  document.querySelector('#browseActions').classList.remove('hidden');
  document.querySelectorAll('.nav-item').forEach(item =>
    item.classList.toggle('active', item.id === 'accountsNav')
  );
  document.querySelectorAll('video').forEach(video => video.pause());
  observer.disconnect(); activeCard = null;
  feed.classList.add('hidden');
  document.querySelector('#accountsPage').classList.remove('hidden');
  document.querySelector('#accountPageBar').classList.add('hidden');
  document.querySelector('#relatedAccounts').classList.add('hidden');
  try {
    const value = await api('/api/accounts');
    accountList = value.accounts;
    renderAccountList();
  } catch (error) {
    document.querySelector('#accountsGrid').innerHTML =
      `<div class="accounts-empty">Could not load accounts: ${error.message}</div>`;
  }
}

function openAccount(post, sourceCard = null) {
  document.querySelector('#browseActions').classList.add('hidden');
  accountListReturn = !document.querySelector('#accountsPage').classList.contains('hidden');
  document.querySelector('#accountsPage').classList.add('hidden');
  feed.classList.remove('hidden');
  if (!selectedAccount && !collectionPage && filter === 'all' && sourceCard) {
    const video = currentVideo(sourceCard);
    homeReturn = {
      key: post.key,
      position: Number(sourceCard.dataset.position) || 0,
      mediaPage: Number(sourceCard.dataset.mediaPage) || 0,
      videoTime: video?.currentTime || 0,
      wasPaused: video ? video.paused : true,
    };
  }
  filter = 'all';
  document.querySelectorAll('[data-account-filter]').forEach(button =>
    button.classList.toggle('active', button.dataset.accountFilter === 'all'));
  document.querySelectorAll('.nav-item').forEach(item =>
    item.classList.toggle('active', item.dataset.filter === 'all'));
  selectedAccount = {
    id: post.account_id, platform: post.platform,
    name: post.account, avatar: post.avatar,
  };
  collectionPage = null;
  document.querySelector('#accountPageBar').classList.remove('collection-page');
  document.querySelector('#accountPageBar').classList.remove('deleted-collection');
  document.querySelector('#accountPageAvatar').src = post.avatar;
  document.querySelector('#accountPageName').textContent = post.account;
  document.querySelector('#accountPageMeta').textContent = `${post.platform} · loading posts…`;
  document.querySelector('#accountPageBar').classList.remove('hidden');
  loadRelatedAccounts();
  setAccountView('grid');
  reloadFeed();
}

function closeAccount() {
  if ((selectedAccount || collectionPage) && accountView !== 'grid') {
    setAccountView('grid');
    if (accountGridReturn) {
      pendingGridFocus = accountGridReturn;
      accountGridReturn = null;
      reloadFeed(0);
    } else reloadFeed();
    return;
  }
  selectedAccount = null;
  collectionPage = null;
  document.querySelector('#browseActions').classList.remove('hidden');
  filter = 'all';
  document.querySelectorAll('.nav-item').forEach(item =>
    item.classList.toggle('active', item.dataset.filter === 'all'));
  document.querySelector('#accountPageBar').classList.add('hidden');
  document.querySelector('#relatedAccounts').classList.add('hidden');
  feed.classList.remove('grid-mode');
  if (accountListReturn) {
    accountListReturn = false;
    showAccountsPage();
    return;
  }
  if (homeReturn) {
    pendingPlaybackFocus = homeReturn;
    const start = Math.max(0, homeReturn.position - 6);
    homeReturn = null;
    reloadFeed(start);
  } else {
    reseedFeed();
    reloadFeed();
  }
}

function openCollection(name) {
  document.querySelector('#browseActions').classList.add('hidden');
  selectedAccount = null;
  collectionPage = name;
  const labels = {liked: 'Liked posts', saved: 'Saved posts', disliked: 'Deleted posts'};
  const label = labels[name] || 'Posts';
  document.querySelector('#accountPageName').textContent = label;
  document.querySelector('#accountPageMeta').textContent = 'loading posts…';
  document.querySelector('#accountPageBar').classList.add('collection-page');
  document.querySelector('#accountPageBar').classList.toggle('deleted-collection', name === 'disliked');
  document.querySelector('#accountPageBar').classList.remove('hidden');
  document.querySelector('#relatedAccounts').classList.add('hidden');
  setAccountView('grid');
  reloadFeed();
}

async function loadRelatedAccounts() {
  if (!selectedAccount) return;
  const requested = `${selectedAccount.platform}:${selectedAccount.id}`;
  const panel = document.querySelector('#relatedAccounts');
  const list = document.querySelector('#relatedList');
  list.innerHTML = '<div class="related-empty">Loading group…</div>';
  panel.classList.remove('hidden');
  try {
    const data = await api(`/api/accounts/${encodeURIComponent(selectedAccount.platform)}/${encodeURIComponent(selectedAccount.id)}/related`);
    if (!selectedAccount || `${selectedAccount.platform}:${selectedAccount.id}` !== requested) return;
    document.querySelector('#relatedHeading').textContent = data.group
      ? `Other accounts · ${data.group}` : 'Other accounts';
    list.innerHTML = '';
    if (!data.accounts.length) {
      list.innerHTML = '<div class="related-empty">No other accounts in this group.</div>';
      return;
    }
    data.accounts.forEach(account => {
      const button = document.createElement('button');
      button.className = 'related-account';
      button.innerHTML = '<img alt=""><div><strong></strong><small></small></div>';
      button.querySelector('img').src = account.avatar;
      button.querySelector('img').alt = account.account;
      button.querySelector('strong').textContent = account.account;
      button.querySelector('small').textContent = `${account.platform} · ${account.posts.toLocaleString()} posts`;
      button.addEventListener('click', () => openAccount(account));
      list.appendChild(button);
    });
  } catch (error) {
    list.innerHTML = '<div class="related-empty">Could not load group accounts.</div>';
  }
}

function isGridPage() {
  return Boolean(selectedAccount || collectionPage) && accountView === 'grid';
}

function setAccountView(view) {
  accountView = view;
  document.querySelectorAll('[data-account-view]').forEach(button =>
    button.classList.toggle('active', button.dataset.accountView === view));
  feed.classList.toggle('grid-mode', view === 'grid');
}

function setAccountFilter(nextFilter) {
  if (!selectedAccount) return;
  filter = nextFilter;
  document.querySelectorAll('[data-account-filter]').forEach(button =>
    button.classList.toggle('active', button.dataset.accountFilter === nextFilter));
  reloadFeed();
}

function startAccountPlayback(order, start = 0, key = '') {
  if (order === 'random') {
    reseedFeed();
    pendingPlaybackFocus = null;
  }
  setAccountView(order);
  if (order === 'newest' && key) {
    pendingPlaybackFocus = {key, position: start, mediaPage: 0, loadFromStart: true};
    reloadFeed(0);
  } else reloadFeed(start);
}

function reseedFeed() {
  feedSeed = crypto.randomUUID();
  sessionStorage.setItem('viewer-seed', feedSeed);
}

const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    const card = entry.target;
    if (entry.isIntersecting && entry.intersectionRatio > .65) {
      activeCard = card;
      document.querySelectorAll('video').forEach(video => { if (!card.contains(video)) video.pause(); });
      const video = currentVideo(card);
      if (video) {
        if (card.dataset.restorePaused === 'true') {
          video.pause(); delete card.dataset.restorePaused;
        } else video.play().catch(() => {});
      }
      // Every playback refreshes recency; it is the weighting signal for the
      // next randomized session, not merely a one-time seen flag.
      saveState(card, {viewed: true});
      const cards = [...feed.querySelectorAll('.post-card')];
      if (!ended && cards.indexOf(card) >= cards.length - 3) loadMore();
    }
  });
}, {root: feed, threshold: [.65]});

async function loadMore() {
  if (loading || ended) return;
  const generation = feedGeneration;
  loading = true;
  let loader = document.createElement('div'); loader.className = 'loader'; loader.textContent = 'Loading archive…'; feed.appendChild(loader);
  try {
    const accountQuery = selectedAccount
      ? `&account_id=${encodeURIComponent(selectedAccount.id)}` : '';
    const selectedPlatform = selectedAccount ? selectedAccount.platform : platform;
    const libraryPage = Boolean(selectedAccount || collectionPage);
    const order = libraryPage && accountView !== 'random' ? 'newest' : 'random';
    const pageSize = isGridPage() ? 60 : 16;
    const pageStart = offset;
    const data = await api(`/api/feed?filter=${encodeURIComponent(filter)}&platform=${encodeURIComponent(selectedPlatform)}${accountQuery}&seed=${encodeURIComponent(feedSeed)}&order=${order}&offset=${offset}&limit=${pageSize}`);
    // A navigation may have replaced the feed while this request was in
    // flight. Never insert relative to a loader from the previous feed.
    if (generation !== feedGeneration) {
      loader.remove();
      return;
    }
    total = data.total;
    data.items.forEach((post, index) => feed.insertBefore(
      isGridPage()
        ? createGridPost(post, pageStart + index) : createPost(post, pageStart + index),
      loader,
    ));
    offset += data.items.length;
    if (selectedAccount) {
      const filterLabel = filter === 'liked' ? ' liked' : filter === 'saved' ? ' saved' : '';
      document.querySelector('#accountPageMeta').textContent =
        `${selectedAccount.platform} · ${total.toLocaleString()}${filterLabel} posts`;
    } else if (collectionPage) {
      document.querySelector('#accountPageMeta').textContent =
        `${total.toLocaleString()} ${collectionPage} posts`;
    }
    ended = offset >= total; if (!data.items.length && offset === 0) showEmpty();
    if (pendingPlaybackFocus) {
      const focus = pendingPlaybackFocus;
      const card = [...feed.querySelectorAll('.post-card')].find(
        item => item.dataset.key === focus.key
      );
      if (card) {
        pendingPlaybackFocus = null;
        requestAnimationFrame(() => {
          const strip = card.querySelector('.media-strip');
          const page = Math.max(0, Math.min(
            strip.children.length - 1, Number(focus.mediaPage) || 0,
          ));
          card.dataset.mediaPage = String(page);
          strip.scrollLeft = page * strip.clientWidth;
          if (focus.wasPaused) card.dataset.restorePaused = 'true';
          card.scrollIntoView({behavior: 'auto', block: 'start'});
          activeCard = card;
          const video = currentVideo(card);
          if (video && focus.videoTime) {
            video.currentTime = focus.videoTime;
          }
        });
      }
    }
    if (pendingGridFocus) {
      const focus = pendingGridFocus;
      const card = [...feed.querySelectorAll('.grid-post')].find(
        item => item.dataset.key === focus.key
      );
      if (card) {
        pendingGridFocus = null;
        requestAnimationFrame(() => {
          feed.scrollTop = focus.scrollTop;
          card.focus({preventScroll: true});
        });
      }
    }
  } catch (error) {
    if (generation !== feedGeneration) return;
    toast(`Could not load archive: ${error.message}`);
  }
  loader.remove(); loading = false;
  if ((pendingPlaybackFocus?.loadFromStart || pendingGridFocus) && !ended) loadMore();
}

function showEmpty() { feed.innerHTML = '<div class="empty"><div><strong>No posts here</strong>Try another filter or rescan the archive.</div></div>'; }
function reloadFeed(start = 0) {
  feedGeneration += 1;
  loading = false;
  observer.disconnect(); feed.innerHTML = ''; offset = start;
  total = 0; ended = false; activeCard = null; loadMore();
}

document.querySelectorAll('.nav-item[data-filter]').forEach(button => button.addEventListener('click', () => {
  document.querySelector('#browseActions').classList.remove('hidden');
  document.querySelector('#accountsPage').classList.add('hidden');
  feed.classList.remove('hidden');
  document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
  button.classList.add('active'); filter = button.dataset.filter;
  if (filter === 'liked' || filter === 'saved' || filter === 'disliked') {
    openCollection(filter);
    return;
  }
  selectedAccount = null;
  collectionPage = null;
  document.querySelector('#accountPageBar').classList.add('hidden');
  document.querySelector('#relatedAccounts').classList.add('hidden');
  feed.classList.remove('grid-mode');
  if (filter === 'all') reseedFeed();
  reloadFeed();
}));
document.querySelector('#accountsNav').addEventListener('click', showAccountsPage);
document.querySelector('#accountsSearch').addEventListener('input', renderAccountList);
document.querySelector('#accountBack').addEventListener('click', closeAccount);
document.querySelectorAll('[data-account-filter]').forEach(button => button.addEventListener('click', () => {
  setAccountFilter(button.dataset.accountFilter);
}));
document.querySelectorAll('[data-account-view]').forEach(button => button.addEventListener('click', () => {
  if (button.dataset.accountView === 'grid') {
    setAccountView('grid'); reloadFeed();
  } else startAccountPlayback(button.dataset.accountView);
}));
feed.addEventListener('scroll', () => {
  if (isGridPage()
      && feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 500) loadMore();
}, {passive: true});
feed.addEventListener('pointermove', wakePostInfoNearTitle, {passive: true});
document.addEventListener('pointermove', event => {
  const drawer = document.querySelector('#relatedAccounts');
  if (!drawer.classList.contains('hidden') && event.clientX >= window.innerWidth - 32) {
    drawer.classList.add('edge-open');
  } else if (!drawer.classList.contains('hidden')
      && event.clientX < window.innerWidth - drawer.offsetWidth - 12) {
    drawer.classList.remove('edge-open');
  }
}, {passive: true});
document.documentElement.dataset.theme = localStorage.getItem('viewer-theme') || 'dark';
window.addEventListener('pywebviewready', () => {
  applyNativeTheme(document.documentElement.dataset.theme);
  setTimeout(() => applyNativeTheme(document.documentElement.dataset.theme), 300);
});

function keyboardCard() {
  const centered = document.elementFromPoint(
    Math.round(window.innerWidth / 2), Math.round(window.innerHeight / 2),
  )?.closest('.post-card');
  return centered || (activeCard?.isConnected ? activeCard : null);
}

function startRightHold(video, tapAction = null) {
  if (rightHoldVideo) return;
  rightHoldVideo = video;
  rightHoldActive = false;
  rightHoldWasPaused = video.paused;
  rightHoldPreviousRate = video.playbackRate || 1;
  rightHoldTapAction = tapAction;
  rightHoldTimer = setTimeout(() => {
    if (rightHoldVideo !== video) return;
    rightHoldActive = true;
    video.playbackRate = 2;
    video.play().catch(() => {});
  }, 280);
}

function finishRightHold(commitTap = true) {
  if (!rightHoldVideo) return;
  const video = rightHoldVideo;
  clearTimeout(rightHoldTimer); rightHoldTimer = null;
  if (rightHoldActive) {
    video.playbackRate = rightHoldPreviousRate;
    if (rightHoldWasPaused) video.pause();
  } else if (commitTap) {
    if (rightHoldTapAction) rightHoldTapAction();
    else {
      const duration = Number.isFinite(video.duration) ? video.duration : Infinity;
      video.currentTime = Math.max(0, Math.min(duration, video.currentTime + 5));
      const card = video.closest('.post-card');
      if (card) updateProgress(card);
    }
  }
  rightHoldVideo = null; rightHoldActive = false; rightHoldTapAction = null;
}

document.addEventListener('keydown', event => {
  if (event.target.closest?.('input, select, textarea')) return;
  if (event.key === 'Escape'
      && document.documentElement.classList.contains('native-fullscreen')) {
    event.preventDefault();
    toggleFullscreen(activeCard);
    return;
  }
  const card = keyboardCard();
  if (!card) return;
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault(); const sibling = event.key === 'ArrowDown' ? card.nextElementSibling : card.previousElementSibling;
    if (sibling?.classList.contains('post-card')) sibling.scrollIntoView({behavior: 'smooth'});
  }
  if (event.key === 'ArrowRight') {
    event.preventDefault();
    const hasMultipleMedia = (card.post?.media?.length || 0) > 1;
    const video = currentVideo(card);
    if (hasMultipleMedia) {
      if (!event.repeat) {
        if (video) startRightHold(video, () => moveMedia(card, 1));
        else moveMedia(card, 1);
      }
    } else if (video) {
      if (!event.repeat) startRightHold(video);
    } else {
      moveMedia(card, 1);
    }
  }
  if (event.key === 'ArrowLeft') {
    event.preventDefault();
    const hasMultipleMedia = (card.post?.media?.length || 0) > 1;
    const video = currentVideo(card);
    if (hasMultipleMedia) {
      if (!event.repeat) moveMedia(card, -1);
    } else if (video) {
      const duration = Number.isFinite(video.duration) ? video.duration : Infinity;
      video.currentTime = Math.max(0, Math.min(duration, video.currentTime - 5));
      updateProgress(card);
    } else moveMedia(card, -1);
  }
  if (event.key.toLowerCase() === 'l') handleAction(card, 'like');
  if (event.key.toLowerCase() === 'd') handleAction(card, 'dislike');
  if (event.key.toLowerCase() === 's') handleAction(card, 'save');
  if (event.key.toLowerCase() === 'f') handleAction(card, 'fullscreen');
  if (event.code === 'Space') { event.preventDefault(); const video = currentVideo(card); if (video) togglePlayback(video); }
});
document.addEventListener('keyup', event => {
  if (event.key === 'ArrowRight') finishRightHold(true);
});
window.addEventListener('blur', () => {
  finishRightHold(false);
  document.querySelectorAll('video').forEach(video => video.pause());
});
window.addEventListener('message', event => {
  if (event.data?.type === 'archiver:pause-viewer') {
    document.querySelectorAll('video').forEach(video => video.pause());
  }
  if (event.data?.type === 'archiver:viewer-fullscreen-changed') {
    applyMonitorFullscreen(Boolean(event.data.fullscreen));
  }
});

refreshStats().catch(() => {});
const requestedFile = launchParams.get('file');
const requestedAccount = launchParams.get('account');
if (requestedFile) {
  api(`/api/locate?path=${encodeURIComponent(requestedFile)}`).then(located => {
    openAccount(located);
    setAccountView('newest');
    pendingPlaybackFocus = {
      key: located.key, position: located.position, mediaPage: 0, loadFromStart: true,
    };
    reloadFeed(Math.max(0, located.position - 6));
  }).catch(error => {
    toast(`Could not open post: ${error.message}`);
    loadMore();
  });
} else if (requestedAccount) {
  const separator = requestedAccount.indexOf(':');
  const requestedPlatform = requestedAccount.slice(0, separator);
  const requestedId = requestedAccount.slice(separator + 1);
  api('/api/accounts').then(value => {
    const account = value.accounts.find(item =>
      item.platform === requestedPlatform && item.account_id === requestedId
    );
    if (account) openAccount(account);
    else loadMore();
  }).catch(() => { loadMore(); });
} else loadMore();

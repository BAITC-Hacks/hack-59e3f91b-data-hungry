/*!
 * EKT AI Assistant - embeddable chat widget for ekt.kz.
 * Single file, vanilla JS, Shadow DOM, no build step, no dependencies.
 *
 * Embed:
 *   <script src="https://host/widget/widget.js" data-api="https://host" data-lang="ru" data-open="1" data-title="..."></script>
 * Public API:
 *   window.EktAssistant = { open(), close(), send(text) }
 *
 * Sections: 1 config | 2 i18n | 3 styles | 4 state & storage | 5 api | 6 markdown | 7 render | 8 events | 9 native cart | 10 boot
 */
(function () {
  'use strict';
  if (window.EktAssistant) return; // loaded twice (e.g. bookmarklet on a page that already has it)

  // ============================================================ 1. CONFIG
  var script = document.currentScript || (function () {
    var all = document.querySelectorAll('script[src*="widget.js"]');
    return all[all.length - 1];
  })();
  var ds = (script && script.dataset) || {};
  var scriptOrigin = (function () {
    try { return new URL(script.src, location.href).origin; } catch (e) { return location.origin; }
  })();
  var CONFIG = {
    api: (ds.api || scriptOrigin).replace(/\/+$/, ''),
    lang: ds.lang === 'kk' ? 'kk' : 'ru',
    openOnLoad: ['1', 'true', 'yes'].indexOf(String(ds.open || '').toLowerCase()) !== -1,
    title: ds.title || '',
    timeoutMs: 60000,
    sessionKey: 'ekt_ai_session',
    convKey: 'ekt_ai_conv',
    accept: '.jpg,.jpeg,.png,.webp,.xlsx,.xls,.docx,.pdf',
    maxUploadMb: 15,
    nativeCart: /(^|\.)ekt\.kz$/i.test(location.hostname),
    nativeCartUrl: 'https://ekt.kz/personal/cart/',
    nativeBasketEndpoint: '/local/templates/template/ajax/basket.php',
    mobileQuery: '(max-width: 640px)'
  };

  // ============================================================ 2. I18N
  var I18N = {
    ru: {
      title: 'Ассистент EKT',
      welcome: 'Здравствуйте! Помогу найти товар, проверить наличие, подобрать аналог или добавить в корзину.',
      placeholder: 'Напишите сообщение…',
      send: 'Отправить', attach: 'Прикрепить файл', close: 'Закрыть', open: 'Открыть чат',
      manager: 'Менеджер', managerMsg: 'Позовите менеджера',
      you: 'Вы', assistant: 'Ассистент', typing: 'Ассистент печатает…',
      chips: [
        { label: 'Наличие по артикулу', prefill: 'Есть ли в наличии артикул ' },
        { label: 'Подобрать аналог', send: 'Подобрать аналог' },
        { label: 'Условия доставки и оплаты', send: 'Условия доставки и оплаты' },
        { label: 'Загрузить спецификацию', file: true }
      ],
      art: 'Арт.', addToCart: 'Добавить в корзину', onSite: 'На сайте ↗',
      inStock: 'В наличии: {n} шт', outOfStock: 'Нет в наличии', onOrder: 'Под заказ',
      certs: 'Сертификаты', addMsg: 'Добавь в корзину: {name} (id {id}), 1 шт',
      pendingTitle: 'Добавить в корзину?', confirm: 'Подтвердить', cancel: 'Отмена', max: 'макс. {n}', pcs: 'шт',
      cartItems: ['позиция', 'позиции', 'позиций'], cartOpen: 'Открыть ↗', cartLink: 'Открыть корзину ↗',
      escalation: 'Связаться с менеджером', phone: 'Телефон', whatsapp: 'WhatsApp', email: 'E-mail',
      errorSend: 'Не удалось отправить.', errorTimeout: 'Сервер не ответил за 60 секунд.', retry: 'Повторить',
      errorUpload: 'Не удалось загрузить файл.', errorTooBig: 'Файл больше 15 МБ.', uploading: 'Загрузка…',
      lookAttachment: 'Посмотри вложение'
    },
    kk: {
      title: 'EKT көмекшісі',
      welcome: 'Сәлеметсіз бе! Тауар табуға, қоймадағы қалдықты тексеруге, аналог таңдауға немесе себетке қосуға көмектесемін.',
      placeholder: 'Хабарлама жазыңыз…',
      send: 'Жіберу', attach: 'Файл тіркеу', close: 'Жабу', open: 'Чатты ашу',
      manager: 'Менеджер', managerMsg: 'Позовите менеджера',
      you: 'Сіз', assistant: 'Көмекші', typing: 'Көмекші жазып жатыр…',
      chips: [
        { label: 'Артикул бойынша қалдық', prefill: 'Қоймада бар ма, артикул ' },
        { label: 'Аналог таңдау', send: 'Аналог таңдау' },
        { label: 'Жеткізу және төлем шарттары', send: 'Жеткізу және төлем шарттары' },
        { label: 'Спецификация жүктеу', file: true }
      ],
      art: 'Арт.', addToCart: 'Себетке қосу', onSite: 'Сайтта ↗',
      inStock: 'Қоймада: {n} дана', outOfStock: 'Қоймада жоқ', onOrder: 'Тапсырыспен',
      certs: 'Сертификаттар', addMsg: 'Добавь в корзину: {name} (id {id}), 1 шт',
      pendingTitle: 'Себетке қосу керек пе?', confirm: 'Растау', cancel: 'Бас тарту', max: 'макс. {n}', pcs: 'дана',
      cartItems: ['тауар', 'тауар', 'тауар'], cartOpen: 'Ашу ↗', cartLink: 'Себетті ашу ↗',
      escalation: 'Менеджермен байланысу', phone: 'Телефон', whatsapp: 'WhatsApp', email: 'E-mail',
      errorSend: 'Жіберу мүмкін болмады.', errorTimeout: 'Сервер 60 секунд ішінде жауап бермеді.', retry: 'Қайталау',
      errorUpload: 'Файл жүктелмеді.', errorTooBig: 'Файл 15 МБ-тан үлкен.', uploading: 'Жүктелуде…',
      lookAttachment: 'Тіркемені қара'
    }
  };
  /** Current dictionary. */
  function t() { return I18N[state.lang] || I18N.ru; }
  /** Tiny template: fill('{n} шт', {n: 3}). */
  function fill(s, vars) { return s.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] != null ? vars[k] : m; }); }

  // ============================================================ 3. STYLES
  var CSS = [
    ':host{all:initial}*{box-sizing:border-box}[hidden]{display:none!important}',
    '.root{--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--soft:#f1f5f9;--accent:#ff6a00;--accent-2:#e65e00;--green:#16a34a;--green-bg:#dcfce7;--amber:#b45309;--amber-bg:#fef3c7;--red-bg:#fee2e2;--radius:16px;',
    'font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;color:var(--ink);-webkit-font-smoothing:antialiased}',
    'button{font:inherit;color:inherit;cursor:pointer;border:0;background:none;padding:0;margin:0}a{color:var(--accent-2)}svg{display:block}',
    // launcher
    '.launcher{position:fixed;right:24px;bottom:24px;z-index:2147483000;width:56px;height:56px;border-radius:50%;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;box-shadow:0 8px 24px rgba(255,106,0,.35);transition:transform .15s}',
    '.launcher:hover{transform:scale(1.06)}.launcher svg{width:28px;height:28px}',
    '.launcher .badge{position:absolute;top:-2px;right:-2px;min-width:20px;height:20px;padding:0 6px;border-radius:10px;background:var(--ink);color:#fff;font-size:12px;font-weight:600;display:flex;align-items:center;justify-content:center;border:2px solid #fff}',
    // panel
    '.panel{position:fixed;right:24px;bottom:96px;z-index:2147483001;width:400px;height:640px;max-height:calc(100vh - 120px);background:#fff;border-radius:var(--radius);box-shadow:0 12px 40px rgba(15,23,42,.18),0 0 0 1px rgba(15,23,42,.06);display:flex;flex-direction:column;overflow:hidden;opacity:0;transform:translateY(12px);pointer-events:none;transition:opacity .18s,transform .18s}',
    '.root.open .panel{opacity:1;transform:none;pointer-events:auto}',
    '.head{height:48px;flex:0 0 48px;display:flex;align-items:center;gap:6px;padding:0 8px 0 14px;border-bottom:1px solid var(--line)}',
    '.dot{width:10px;height:10px;border-radius:50%;background:var(--green);box-shadow:0 0 0 3px var(--green-bg);flex:0 0 10px}',
    '.title{font-weight:600;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-left:4px}',
    '.lang{display:flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;flex:0 0 auto}.lang button{padding:4px 8px;font-size:12px;font-weight:600;color:var(--muted)}.lang button.on{background:var(--ink);color:#fff}',
    '.mgr{display:flex;align-items:center;gap:4px;padding:5px 8px;border-radius:8px;font-size:12px;font-weight:600;border:1px solid var(--line);white-space:nowrap}.mgr:hover{background:var(--soft)}.mgr svg{width:14px;height:14px}',
    '.icon-btn{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;color:var(--muted);flex:0 0 32px}.icon-btn:hover{background:var(--soft);color:var(--ink)}.icon-btn svg{width:18px;height:18px}',
    // messages
    '.msgs{flex:1;overflow-y:auto;padding:14px 12px;display:flex;flex-direction:column;gap:10px;background:#fff}',
    '.msg{display:flex;flex-direction:column;max-width:92%}.msg.user{align-self:flex-end;align-items:flex-end}.msg.assistant{align-self:flex-start}.msg.wide{max-width:100%;width:100%}',
    '.who{font-size:11px;color:var(--muted);margin:0 4px 3px}',
    '.bubble{padding:9px 12px;border-radius:14px;background:var(--soft);overflow-wrap:anywhere;word-wrap:break-word}',
    '.msg.user .bubble{background:var(--accent);color:#fff;border-bottom-right-radius:4px}.msg.user .bubble a{color:#fff}.msg.assistant .bubble{border-bottom-left-radius:4px}',
    '.bubble p{margin:0}.bubble p+p,.bubble p+ul,.bubble p+ol,.bubble ul+p,.bubble ol+p{margin-top:6px}.bubble ul,.bubble ol{margin:0;padding-left:18px}.bubble li+li{margin-top:2px}',
    '.bubble code{font-family:ui-monospace,Menlo,monospace;font-size:12px;background:rgba(15,23,42,.06);padding:1px 4px;border-radius:4px}.bubble a{word-break:break-all}',
    '.files{font-size:12px;color:var(--muted);margin-top:3px}',
    '.typing{display:inline-flex;gap:4px;padding:13px 14px}.typing i{width:7px;height:7px;border-radius:50%;background:#94a3b8;animation:ektb 1.2s infinite}.typing i:nth-child(2){animation-delay:.2s}.typing i:nth-child(3){animation-delay:.4s}',
    '@keyframes ektb{0%,80%,100%{opacity:.3;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}',
    '.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{padding:7px 12px;border-radius:999px;border:1px solid var(--accent);color:var(--accent-2);font-size:13px;font-weight:500;background:#fff}.chip:hover{background:#fff4ec}',
    // product cards
    '.cards{display:flex;flex-direction:column;gap:8px;margin-top:8px;width:100%}',
    '.card{border:1px solid var(--line);border-radius:12px;padding:10px;background:#fff}.card-top{display:flex;gap:10px}',
    '.thumb{width:64px;height:64px;flex:0 0 64px;border-radius:8px;background:var(--soft);overflow:hidden;display:flex;align-items:center;justify-content:center;color:#94a3b8}.thumb img{width:100%;height:100%;object-fit:contain}.thumb svg{width:28px;height:28px}',
    '.card-info{min-width:0;flex:1}.name{font-weight:600;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}',
    '.meta{font-size:12px;color:var(--muted);margin-top:2px}.price-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:4px}.price{font-weight:700;font-size:15px}',
    '.badge{font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;white-space:nowrap}.badge.green{color:#166534;background:var(--green-bg)}.badge.grey{color:#475569;background:var(--soft)}.badge.amber{color:var(--amber);background:var(--amber-bg)}',
    '.stores,.certs,.reason{font-size:12px;margin-top:6px}.stores{color:var(--muted)}.certs a{display:flex;align-items:center;gap:4px;margin-top:2px}.certs svg{width:13px;height:13px;flex:0 0 13px}',
    '.reason{background:#f8fafc;border-left:3px solid var(--accent);padding:6px 8px;border-radius:0 8px 8px 0}',
    '.card-actions{display:flex;gap:6px;margin-top:8px}',
    '.btn{flex:1;padding:8px 10px;border-radius:10px;font-size:13px;font-weight:600;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:4px;white-space:nowrap}',
    '.btn.primary{background:var(--accent);color:#fff}.btn.primary:hover{background:var(--accent-2)}.btn.ghost{border:1px solid var(--line);color:var(--ink)}.btn.ghost:hover{background:var(--soft)}.btn:disabled{opacity:.5;cursor:default}',
    // pending action / escalation / cart link
    '.pending{border:2px solid #f59e0b;background:#fffbeb;border-radius:12px;padding:10px;width:100%}.pending h4{margin:0 0 4px;font-size:14px}',
    '.pitem{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;border-top:1px dashed #fcd34d;font-size:13px}.pname{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.pqty{font-weight:700;white-space:nowrap}.pmax{font-size:11px;color:var(--muted);white-space:nowrap}.pactions{display:flex;gap:6px;margin-top:8px}',
    '.escal{border:1px solid var(--line);border-radius:12px;padding:10px;margin-top:8px;font-size:13px;background:#f8fafc;width:100%}.escal b{display:block;margin-bottom:4px}.escal a{display:inline-block;margin-right:12px}',
    '.cartlink{margin-top:8px}',
    // bars
    '.cartbar{display:flex;align-items:center;gap:8px;padding:8px 12px;border-top:1px solid var(--line);background:#fff7f0;font-size:13px}.cartbar svg{width:18px;height:18px;color:var(--accent-2)}.grow{flex:1;min-width:0}.cartbar a{font-weight:600;text-decoration:none;white-space:nowrap}',
    '.errbar{display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--red-bg);color:#991b1b;font-size:13px;border-top:1px solid #fecaca}.errbar button{font-weight:600;text-decoration:underline;white-space:nowrap}',
    // composer
    '.composer{border-top:1px solid var(--line);padding:8px 10px;background:#fff}',
    '.attach-list{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px}.attach-list:empty{display:none}',
    '.achip{display:inline-flex;align-items:center;gap:6px;padding:4px 8px;border-radius:8px;background:var(--soft);font-size:12px;max-width:100%}.achip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.achip small{color:var(--muted);white-space:nowrap}.achip button{color:var(--muted);font-size:14px;line-height:1}',
    '.row{display:flex;align-items:flex-end;gap:6px}',
    'textarea{flex:1;min-width:0;resize:none;border:1px solid var(--line);border-radius:12px;padding:9px 12px;font:inherit;font-size:14px;line-height:1.35;color:var(--ink);background:#fff;max-height:120px;outline:none}',
    'textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(255,106,0,.15)}textarea::placeholder{color:#94a3b8}',
    '.send,.clip{width:40px;height:40px;flex:0 0 40px;border-radius:12px;display:flex;align-items:center;justify-content:center}.send{background:var(--accent);color:#fff}.send:hover{background:var(--accent-2)}.send:disabled{opacity:.5;cursor:default}.send svg,.clip svg{width:20px;height:20px}',
    '.clip{color:var(--muted)}.clip:hover{background:var(--soft);color:var(--ink)}',
    // mobile
    '@media ' + CONFIG.mobileQuery + '{.panel{top:0;right:0;bottom:0;left:0;width:100%;height:100dvh;max-height:none;border-radius:0;padding-top:env(safe-area-inset-top)}',
    '.composer{padding-bottom:calc(8px + env(safe-area-inset-bottom))}textarea{font-size:16px}.root.open .launcher{display:none}.mgr span{display:none}.mgr{padding:7px}}'
  ].join('');

  var ICON = {
    chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-6.5A8 8 0 0 1 13 4a8 8 0 0 1 8 8z"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    send: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 11.5 21 3l-7 18-2.5-7.5z"/></svg>',
    clip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m21 11-8.5 8.5a5 5 0 0 1-7-7L14 4a3.3 3.3 0 0 1 4.7 4.7L10.5 17a1.6 1.6 0 0 1-2.3-2.3L16 7"/></svg>',
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
    cart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4h2l2.5 11h11L21 7H7"/><circle cx="9" cy="20" r="1.5"/><circle cx="17" cy="20" r="1.5"/></svg>',
    doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2h8l6 6v14H6z"/><path d="M14 2v6h6M9 13h6M9 17h6"/></svg>',
    box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M3 7l9 4 9-4M12 11v10"/></svg>'
  };

  // ============================================================ 4. STATE & STORAGE
  var state = {
    open: false,
    lang: CONFIG.lang,
    sessionId: null,
    messages: [],      // {role:'user'|'assistant', text, kind?:'welcome', products?, escalation?, cartUpdated?, files?}
    pending: null,     // pending_action from the API
    cart: null,        // cart object from the API
    attachments: [],   // uploaded files waiting to be sent: {id, filename, summary}
    uploading: false,
    sending: false,
    error: null,       // {message, retry: fn|null}
    lastRequest: null  // fn re-run by "retry"
  };
  var ui = {};

  function storageGet(store, key) { try { return window[store].getItem(key); } catch (e) { return null; } }
  function storageSet(store, key, val) { try { window[store].setItem(key, val); } catch (e) { /* private mode etc. */ } }

  /** Persist session id (localStorage) and the conversation (sessionStorage, survives page navigation). */
  function save() {
    if (state.sessionId) storageSet('localStorage', CONFIG.sessionKey, state.sessionId);
    storageSet('sessionStorage', CONFIG.convKey, JSON.stringify({
      sessionId: state.sessionId, lang: state.lang, open: state.open,
      messages: state.messages.slice(-60), pending: state.pending, cart: state.cart
    }));
  }

  /** Restore state; the cached conversation is only used if it belongs to the stored session. Returns true if restored. */
  function load() {
    state.sessionId = storageGet('localStorage', CONFIG.sessionKey) || null;
    var raw = storageGet('sessionStorage', CONFIG.convKey);
    if (!raw) return false;
    try {
      var c = JSON.parse(raw);
      if ((c.sessionId || null) !== state.sessionId) return false;
      state.lang = c.lang === 'kk' ? 'kk' : 'ru';
      state.messages = Array.isArray(c.messages) ? c.messages : [];
      state.pending = c.pending || null;
      state.cart = c.cart || null;
      state.open = !!c.open;
      return true;
    } catch (e) { return false; }
  }

  // ============================================================ 5. API
  /** fetch() with a configurable timeout and JSON error extraction (FastAPI puts messages in `detail`). */
  function apiFetch(path, options, timeoutMs) {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, timeoutMs || CONFIG.timeoutMs);
    var opts = Object.assign({ signal: ctrl.signal }, options || {});
    return fetch(CONFIG.api + path, opts).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { /* non-JSON body */ }
        if (!res.ok) {
          var msg = (data && (data.detail || data.error || data.message)) || ('HTTP ' + res.status);
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        return data;
      });
    }).finally(function () { clearTimeout(timer); });
  }
  function postJson(path, body) {
    return apiFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  }
  function apiChat(message, attachmentIds) {
    return postJson('/api/chat', { session_id: state.sessionId, message: message, attachment_ids: attachmentIds || [], page_url: location.href, lang: state.lang });
  }
  function apiConfirm(actionId, confirm) {
    return postJson('/api/chat/confirm', { session_id: state.sessionId, action_id: actionId, confirm: !!confirm });
  }
  function apiUpload(file) {
    var fd = new FormData();
    fd.append('file', file, file.name);
    if (state.sessionId) fd.append('session_id', state.sessionId);
    return apiFetch('/api/upload', { method: 'POST', body: fd }, 310000);
  }
  function apiCart(sessionId) { return apiFetch('/api/cart/' + encodeURIComponent(sessionId)); }

  // ============================================================ 6. MARKDOWN (safe mini renderer)
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  /** Only http(s)/mailto/tel/relative URLs are allowed in links (input is already HTML-escaped). */
  function safeUrl(u) {
    var s = String(u || '').trim();
    return /^(https?:\/\/|mailto:|tel:|\/)/i.test(s) ? s : null;
  }
  /** Inline markdown on an escaped line: [text](url), bare URLs, **bold**, *italic*, `code`. */
  function inlineMd(text) {
    var held = [];
    function hold(html) { held.push(html); return '\u0000' + (held.length - 1) + '\u0000'; }
    var out = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (m, label, url) {
      var u = safeUrl(url);
      return u ? hold('<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + label + '</a>') : m;
    });
    out = out.replace(/(^|[\s(])(https?:\/\/[^\s<]*[^\s<.,;:!?)\]'"])/g, function (m, pre, url) {
      return pre + hold('<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + url + '</a>');
    });
    out = out.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    out = out.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    return out.replace(/\u0000(\d+)\u0000/g, function (m, i) { return held[+i]; });
  }
  /** Block markdown: paragraphs (blank line separated, single newlines -> <br>), bullet and numbered lists, # headings. */
  function renderMarkdown(src) {
    var lines = escapeHtml(src).replace(/\r\n?/g, '\n').split('\n');
    var html = '', list = null, para = [];
    function flushPara() { if (para.length) { html += '<p>' + para.join('<br>') + '</p>'; para = []; } }
    function closeList() { if (list) { html += '</' + list + '>'; list = null; } }
    lines.forEach(function (raw) {
      var line = raw.replace(/\s+$/, ''), m;
      if ((m = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/))) {
        flushPara();
        var type = /^\s*\d/.test(line) ? 'ol' : 'ul';
        if (list !== type) { closeList(); list = type; html += '<' + type + '>'; }
        html += '<li>' + inlineMd(m[1]) + '</li>';
        return;
      }
      closeList();
      if (!line.trim()) { flushPara(); return; }
      if ((m = line.match(/^\s*#{1,6}\s+(.*)$/))) { flushPara(); html += '<p><strong>' + inlineMd(m[1]) + '</strong></p>'; return; }
      para.push(inlineMd(line));
    });
    flushPara(); closeList();
    return html;
  }

  // ============================================================ 7. RENDER
  /** Small DOM builder: el('div', {class:'x', onclick: fn}, [children|string]). */
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'class') node.className = v;
      else if (k === 'html') node.innerHTML = v;
      else if (k.indexOf('on') === 0) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : v);
    });
    (children || []).forEach(function (c) {
      if (c == null || c === false) return;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return node;
  }
  function formatPrice(n) {
    if (n == null || isNaN(Number(n))) return '';
    return Math.round(Number(n)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' ₸';
  }
  function plural(n, forms) {
    var a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return forms[2];
    if (b > 1 && b < 5) return forms[1];
    return b === 1 ? forms[0] : forms[2];
  }
  function isMobile() { return window.matchMedia && window.matchMedia(CONFIG.mobileQuery).matches; }
  function cartUrl() { return CONFIG.nativeCart ? CONFIG.nativeCartUrl : (state.cart && state.cart.url) || ''; }

  /** Stock badge: green (in stock, N шт) / grey (none) / amber (stock unknown -> "Под заказ"). */
  function stockBadge(p) {
    var unknown = p.stock_status === 'unknown' || (p.in_stock == null && p.quantity == null);
    if (unknown) return el('span', { class: 'badge amber' }, [t().onOrder]);
    var qty = Number(p.quantity || 0);
    if (p.in_stock || qty > 0) return el('span', { class: 'badge green' }, [qty > 0 ? fill(t().inStock, { n: qty }) : t().inStock.split(':')[0]]);
    return el('span', { class: 'badge grey' }, [t().outOfStock]);
  }

  var brokenImages = {}; // image URLs that failed once: don't re-request them on every re-render

  function renderProduct(p) {
    var thumb = el('div', { class: 'thumb', html: ICON.box });
    if (p.image && !brokenImages[p.image]) {
      var img = el('img', { src: p.image, alt: '' }); // no loading=lazy: a detached lazy image never loads
      img.addEventListener('error', function () { brokenImages[p.image] = true; img.remove(); });
      img.addEventListener('load', function () { thumb.innerHTML = ''; thumb.appendChild(img); });
    }
    var meta = [p.article ? t().art + ' ' + p.article : '', p.brand || ''].filter(Boolean).join(' · ');
    var stores = (p.stores || []).filter(function (s) { return Number(s.quantity) > 0; }).slice(0, 3)
      .map(function (s) { return s.name + ' ' + s.quantity; }).join(' · ');
    var certs = (p.certificates || []).filter(function (c) { return c && safeUrl(c.url); });
    var card = el('div', { class: 'card' }, [
      el('div', { class: 'card-top' }, [
        thumb,
        el('div', { class: 'card-info' }, [
          el('div', { class: 'name', title: p.name || '' }, [p.name || '']),
          meta ? el('div', { class: 'meta' }, [meta]) : null,
          el('div', { class: 'price-row' }, [p.price != null ? el('span', { class: 'price' }, [formatPrice(p.price)]) : null, stockBadge(p)])
        ])
      ]),
      stores ? el('div', { class: 'stores' }, [stores]) : null,
      certs.length ? el('div', { class: 'certs' }, certs.map(function (c) {
        return el('a', { href: safeUrl(c.url), target: '_blank', rel: 'noopener noreferrer', html: ICON.doc }, [c.title || t().certs]);
      })) : null,
      p.reason ? el('div', { class: 'reason' }, [p.reason]) : null,
      el('div', { class: 'card-actions' }, [
        el('button', { class: 'btn primary', type: 'button', onclick: function () {
          sendMessage(fill(t().addMsg, { name: p.name || '', id: p.id }));
        } }, [t().addToCart]),
        safeUrl(p.url) ? el('a', { class: 'btn ghost', href: safeUrl(p.url), target: '_blank', rel: 'noopener noreferrer' }, [t().onSite]) : null
      ])
    ]);
    return card;
  }

  function renderEscalation(e) {
    var c = e.contacts || {};
    var wa = c.whatsapp ? (/^https?:/.test(c.whatsapp) ? c.whatsapp : 'https://wa.me/' + String(c.whatsapp).replace(/\D/g, '')) : null;
    return el('div', { class: 'escal' }, [
      el('b', {}, [t().escalation]),
      e.reason ? el('div', { class: 'meta' }, [e.reason]) : null,
      el('div', {}, [
        c.phone ? el('a', { href: 'tel:' + String(c.phone).replace(/[^\d+]/g, '') }, [c.phone]) : null,
        wa ? el('a', { href: wa, target: '_blank', rel: 'noopener noreferrer' }, [t().whatsapp]) : null,
        c.email ? el('a', { href: 'mailto:' + c.email }, [c.email]) : null
      ])
    ]);
  }

  function renderMessage(m) {
    var isUser = m.role === 'user';
    var text = m.kind === 'welcome' ? t().welcome : (m.text || '');
    var wide = !isUser && ((m.products && m.products.length) || m.escalation || m.cartUpdated);
    var node = el('div', { class: 'msg ' + (isUser ? 'user' : 'assistant') + (wide ? ' wide' : '') }, [
      el('div', { class: 'who' }, [isUser ? t().you : t().assistant]),
      el('div', { class: 'bubble', html: isUser ? escapeHtml(text).replace(/\n/g, '<br>') : renderMarkdown(text) })
    ]);
    if (isUser && m.files && m.files.length) node.appendChild(el('div', { class: 'files' }, ['📎 ' + m.files.join(', ')]));
    if (!isUser && m.products && m.products.length) node.appendChild(el('div', { class: 'cards' }, m.products.map(renderProduct)));
    if (!isUser && m.cartUpdated && cartUrl()) {
      node.appendChild(el('div', { class: 'cartlink' }, [el('a', { class: 'btn primary', href: cartUrl(), target: '_blank', rel: 'noopener noreferrer' }, [t().cartLink])]));
    }
    if (!isUser && m.escalation) node.appendChild(renderEscalation(m.escalation));
    return node;
  }

  function renderPending(pa) {
    var items = (pa.items || []).map(function (it) {
      return el('div', { class: 'pitem' }, [
        el('span', { class: 'pname', title: it.name || '' }, [it.name || ('#' + it.product_id)]),
        el('span', { class: 'pqty' }, [it.qty + ' ' + t().pcs]),
        it.max_qty != null ? el('span', { class: 'pmax' }, [fill(t().max, { n: it.max_qty })]) : null
      ]);
    });
    return el('div', { class: 'pending', role: 'group' }, [
      el('h4', {}, [t().pendingTitle])
    ].concat(items, [
      el('div', { class: 'pactions' }, [
        el('button', { class: 'btn primary', type: 'button', disabled: state.sending, onclick: function () { confirmPending(pa, true); } }, ['✔ ' + t().confirm]),
        el('button', { class: 'btn ghost', type: 'button', disabled: state.sending, onclick: function () { confirmPending(pa, false); } }, [t().cancel])
      ])
    ]));
  }

  function renderChips() {
    return el('div', { class: 'chips' }, t().chips.map(function (c) {
      return el('button', { class: 'chip', type: 'button', onclick: function () {
        if (c.file) ui.file.click();
        else if (c.prefill) { ui.textarea.value = c.prefill; autogrow(); ui.textarea.focus(); ui.textarea.setSelectionRange(c.prefill.length, c.prefill.length); }
        else sendMessage(c.send);
      } }, [c.label]);
    }));
  }

  /** Re-render the dynamic parts (messages, pending block, chips, bars, composer) from state. */
  function render() {
    var d = t();
    ui.root.classList.toggle('open', state.open);
    ui.panel.hidden = !state.open;
    ui.title.textContent = CONFIG.title || d.title;
    ui.panel.setAttribute('aria-label', CONFIG.title || d.title);
    ui.launcher.setAttribute('aria-label', state.open ? d.close : d.open);
    ui.mgrLabel.textContent = d.manager;
    ui.mgr.title = d.managerMsg;
    ui.closeBtn.setAttribute('aria-label', d.close);
    ui.clip.setAttribute('aria-label', d.attach);
    ui.sendBtn.setAttribute('aria-label', d.send);
    ui.textarea.placeholder = d.placeholder;
    ui.langBtns.forEach(function (b) { b.classList.toggle('on', b.dataset.lang === state.lang); });

    // message list
    var stuck = ui.msgs.scrollHeight - ui.msgs.scrollTop - ui.msgs.clientHeight < 40;
    ui.msgs.innerHTML = '';
    state.messages.forEach(function (m) { ui.msgs.appendChild(renderMessage(m)); });
    if (state.messages.length <= 1 && !state.sending) ui.msgs.appendChild(renderChips());
    if (state.pending && !state.sending) ui.msgs.appendChild(renderPending(state.pending));
    if (state.sending) ui.msgs.appendChild(el('div', { class: 'msg assistant', 'aria-label': d.typing }, [el('div', { class: 'bubble typing' }, [el('i'), el('i'), el('i')])]));

    // cart bar + launcher badge
    var count = state.cart ? Number(state.cart.count || 0) : 0;
    ui.cartbar.hidden = !(count > 0);
    if (count > 0) {
      ui.cartText.textContent = count + ' ' + plural(count, d.cartItems) + ' · ' + formatPrice(state.cart.total || 0);
      ui.cartLink.href = cartUrl() || '#';
      ui.cartLink.textContent = d.cartOpen;
    }
    ui.badge.hidden = !(count > 0);
    ui.badge.textContent = count;

    // error banner
    ui.errbar.hidden = !state.error;
    if (state.error) { ui.errText.textContent = state.error.message; ui.retryBtn.hidden = !state.error.retry; ui.retryBtn.textContent = d.retry; }

    // attachments + composer
    ui.attachList.innerHTML = '';
    state.attachments.forEach(function (a, i) {
      ui.attachList.appendChild(el('span', { class: 'achip' }, [
        el('span', {}, [a.filename]), a.summary ? el('small', {}, [a.summary]) : null,
        el('button', { type: 'button', 'aria-label': d.close, onclick: function () { state.attachments.splice(i, 1); render(); } }, ['✕'])
      ]));
    });
    if (state.uploading) ui.attachList.appendChild(el('span', { class: 'achip' }, [el('small', {}, [d.uploading])]));
    ui.sendBtn.disabled = state.sending || state.uploading;
    ui.clip.disabled = state.uploading;
    // scroll last: the bars above change the list height
    if (stuck || state.sending) ui.msgs.scrollTop = ui.msgs.scrollHeight;
  }

  /** Build the static shell once inside the shadow root. */
  function mount() {
    var host = document.createElement('div');
    host.id = 'ekt-ai-assistant';
    host.style.cssText = 'all:initial;position:static;';
    var shadow = host.attachShadow({ mode: 'open' });
    var root = el('div', { class: 'root' });
    var style = document.createElement('style');
    style.textContent = CSS;
    shadow.appendChild(style);
    shadow.appendChild(root);

    ui.root = root;
    ui.badge = el('span', { class: 'badge', hidden: true });
    ui.launcher = el('button', { class: 'launcher', type: 'button', html: ICON.chat, onclick: toggle });
    ui.launcher.appendChild(ui.badge);

    ui.title = el('span', { class: 'title' });
    ui.langBtns = ['ru', 'kk'].map(function (l) {
      return el('button', { type: 'button', 'data-lang': l, onclick: function () { setLang(l); } }, [l === 'ru' ? 'RU' : 'KZ']);
    });
    ui.mgrLabel = el('span');
    ui.mgr = el('button', { class: 'mgr', type: 'button', html: ICON.user, onclick: function () { sendMessage(t().managerMsg); } });
    ui.mgr.appendChild(ui.mgrLabel);
    ui.closeBtn = el('button', { class: 'icon-btn', type: 'button', html: ICON.close, onclick: close });
    var head = el('div', { class: 'head' }, [el('span', { class: 'dot' }), ui.title, el('div', { class: 'lang' }, ui.langBtns), ui.mgr, ui.closeBtn]);

    ui.msgs = el('div', { class: 'msgs', role: 'log', 'aria-live': 'polite', 'aria-relevant': 'additions' });

    ui.cartText = el('span', { class: 'grow' });
    ui.cartLink = el('a', { target: '_blank', rel: 'noopener noreferrer' });
    ui.cartbar = el('div', { class: 'cartbar', hidden: true, html: ICON.cart }, [ui.cartText, ui.cartLink]);

    ui.errText = el('span', { class: 'grow' });
    ui.retryBtn = el('button', { type: 'button', onclick: function () { if (state.error && state.error.retry) state.error.retry(); } });
    ui.errbar = el('div', { class: 'errbar', role: 'alert', hidden: true }, [ui.errText, ui.retryBtn]);

    ui.attachList = el('div', { class: 'attach-list' });
    ui.file = el('input', { type: 'file', accept: CONFIG.accept, hidden: true, onchange: onFilePicked });
    ui.clip = el('button', { class: 'clip', type: 'button', html: ICON.clip, onclick: function () { ui.file.click(); } });
    ui.textarea = el('textarea', { rows: '1', onkeydown: onKeyDown, oninput: autogrow });
    ui.sendBtn = el('button', { class: 'send', type: 'button', html: ICON.send, onclick: function () { sendMessage(ui.textarea.value); } });
    var composer = el('div', { class: 'composer' }, [ui.attachList, el('div', { class: 'row' }, [ui.clip, ui.textarea, ui.sendBtn]), ui.file]);

    ui.panel = el('div', { class: 'panel', role: 'dialog', 'aria-modal': 'false', hidden: true }, [head, ui.msgs, ui.cartbar, ui.errbar, composer]);
    root.appendChild(ui.launcher);
    root.appendChild(ui.panel);
    document.body.appendChild(host);
  }

  // ============================================================ 8. EVENTS & ACTIONS
  function open() {
    state.open = true; render(); save();
    if (!isMobile()) setTimeout(function () { ui.textarea.focus(); }, 50);
  }
  function close() { state.open = false; render(); save(); }
  function toggle() { state.open ? close() : open(); }
  function setLang(l) { state.lang = l === 'kk' ? 'kk' : 'ru'; render(); save(); }
  function autogrow() {
    ui.textarea.style.height = 'auto';
    ui.textarea.style.height = Math.min(ui.textarea.scrollHeight, 120) + 'px';
  }
  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendMessage(ui.textarea.value); }
  }
  function onDocKeyDown(e) { if (e.key === 'Escape' && state.open) close(); }

  /** Run an API call with the sending/typing/error states; `after(res)` runs on success before render. */
  function runRequest(fn, after) {
    if (state.sending) return Promise.resolve();
    state.sending = true; state.error = null; state.lastRequest = fn; render();
    return fn().then(function (res) {
      applyResponse(res || {});
      if (after) after(res || {});
    }).catch(function (err) {
      var msg = err && err.name === 'AbortError' ? t().errorTimeout : t().errorSend + (err && err.message ? ' ' + err.message : '');
      state.error = { message: msg, retry: function () { runRequest(fn, after); } };
    }).then(function () { state.sending = false; render(); save(); });
  }

  /** Merge a ChatResponse into state. */
  function applyResponse(res) {
    if (res.session_id) state.sessionId = res.session_id;
    state.messages.push({
      role: 'assistant', text: res.reply || '', products: res.products || [],
      escalation: res.escalation || null, cartUpdated: !!res.cart_updated
    });
    state.pending = res.pending_action || null;
    if (res.cart) state.cart = res.cart;
  }

  /** Send a chat message (text and/or the uploaded attachments). */
  function sendMessage(text) {
    var msg = String(text || '').trim();
    var ids = state.attachments.map(function (a) { return a.id; });
    if ((!msg && !ids.length) || state.sending || state.uploading) return Promise.resolve();
    var outgoing = msg || t().lookAttachment;
    state.messages.push({ role: 'user', text: outgoing, files: state.attachments.map(function (a) { return a.filename; }) });
    state.attachments = [];
    ui.textarea.value = ''; autogrow();
    if (!state.open) open();
    return runRequest(function () { return apiChat(outgoing, ids); });
  }

  /** Confirm / cancel the pending add-to-cart action; on ekt.kz also push items to the native Bitrix cart. */
  function confirmPending(pa, yes) {
    var items = (pa.items || []).slice();
    state.pending = null;
    return runRequest(function () { return apiConfirm(pa.action_id, yes); }, function (res) {
      if (yes && res.cart_updated && CONFIG.nativeCart) nativeCartAdd(items, res.cart);
    });
  }

  function onFilePicked() {
    var file = ui.file.files && ui.file.files[0];
    ui.file.value = '';
    if (!file) return;
    if (file.size > CONFIG.maxUploadMb * 1024 * 1024) { state.error = { message: t().errorTooBig, retry: null }; render(); return; }
    state.uploading = true; state.error = null; render();
    apiUpload(file).then(function (res) {
      if (res.session_id) state.sessionId = res.session_id;
      state.attachments.push({ id: res.attachment_id, filename: res.filename || file.name, summary: res.summary || '' });
    }).catch(function (err) {
      state.error = { message: t().errorUpload + (err && err.message ? ' ' + err.message : ''), retry: null };
    }).then(function () { state.uploading = false; render(); save(); ui.textarea.focus(); });
  }

  // ============================================================ 9. NATIVE CART (only on *.ekt.kz)
  /**
   * Replays a confirmed add_to_cart into the site's own Bitrix basket, one request per item -
   * the same request the site's "В корзину" button sends. Quantities: the server-clamped qty from
   * the returned cart when available, else the proposed qty clamped to max_qty. Failures are silent.
   */
  function nativeCartAdd(items, cart) {
    var inCart = {};
    ((cart && cart.items) || []).forEach(function (c) { inCart[String(c.product_id)] = Number(c.qty); });
    items.forEach(function (it) {
      var qty = inCart[String(it.product_id)];
      if (!(qty > 0)) qty = Math.max(1, Math.min(Number(it.qty) || 1, Number(it.max_qty) || Infinity));
      fetch(CONFIG.nativeBasketEndpoint, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        body: new URLSearchParams({ action: 'add2basket', id: String(it.product_id), quantity: String(qty), kratnost: '1' })
      }).catch(function () { /* the prototype cart still has the item */ });
    });
  }

  // ============================================================ 10. BOOT
  function boot() {
    var restored = load();
    if (!state.messages.length) state.messages.push({ role: 'assistant', kind: 'welcome' });
    mount();
    document.addEventListener('keydown', onDocKeyDown);
    if (CONFIG.openOnLoad && !restored) state.open = true; // later navigations keep the user's own open/closed choice
    render();
    if (state.open && !isMobile()) ui.textarea.focus();
    // refresh the cart bar for a returning session (quietly)
    if (state.sessionId && !state.cart) {
      apiCart(state.sessionId).then(function (cart) { if (cart && cart.items) { state.cart = cart; render(); save(); } }).catch(function () {});
    }
  }

  window.EktAssistant = {
    open: open,
    close: close,
    /** Send a message programmatically (opens the panel). */
    send: function (text) { return sendMessage(text); }
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();

/*!
 * EKT AI Assistant - embeddable chat widget for ekt.kz.
 * Single file, vanilla JS, Shadow DOM, no build step, no dependencies.
 * Look & feel follows docs/DESIGN.md (tokens taken from the live ekt.kz site).
 *
 * Embed:
 *   <script src="https://host/widget/widget.js" data-api="https://host" data-lang="ru" data-open="1" data-title="..."></script>
 * Public API:
 *   window.EktAssistant = { open(), close(), send(text) }
 *
 * Sections: 1 config | 2 i18n | 3 styles & icons | 4 state & storage | 5 api | 6 markdown | 7 render | 8 events | 9 native cart | 10 boot
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
    mobileQuery: '(max-width: 640px)',
    // PT Sans = the site font. @import inside a shadow root is unreliable, so a <link> goes into document.head (font faces are document-wide).
    fontUrl: 'https://fonts.googleapis.com/css2?family=PT+Sans:wght@400;700&display=swap'
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
      art: 'Арт.', addToCart: 'В корзину', onSite: 'На сайте ↗',
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

  // ============================================================ 3. STYLES & ICONS
  // Design tokens (docs/DESIGN.md) live on the shadow host as --ekt-* custom properties; `all:initial` does not reset them.
  var CSS = [
    ':host{all:initial;--ekt-font:"PT Sans",Helvetica,Arial,sans-serif;--ekt-text:#212529;--ekt-heading:#0b4366;--ekt-blue:#2c7294;--ekt-blue-light:#0b98d0;--ekt-blue-bg:#e8f1f6;',
    '--ekt-yellow:#f4b301;--ekt-yellow-hover:#e0a500;--ekt-orange:#f7941d;--ekt-bg:#f2f2f2;--ekt-surface:#fff;--ekt-border:#ddd;--ekt-border-light:#e6e6e6;--ekt-muted:#888;',
    '--ekt-success:#198754;--ekt-danger:#dc3545;--ekt-radius:3px;--ekt-radius-md:5px;--ekt-radius-lg:8px;--ekt-btn-h:40px}',
    '*{box-sizing:border-box}[hidden]{display:none!important}',
    '.root{font:14px/1.45 var(--ekt-font);color:var(--ekt-text);-webkit-font-smoothing:antialiased}',
    'button{font:inherit;color:inherit;cursor:pointer;border:0;background:none;padding:0;margin:0}a{color:var(--ekt-blue)}svg{display:block}',
    // launcher: yellow circle like the site's primary button; bottom-right (the site's WhatsApp button sits mid-right)
    '.launcher{position:fixed;right:24px;bottom:24px;z-index:2147483000;width:56px;height:56px;border-radius:50%;background:var(--ekt-yellow);color:#fff;display:flex;align-items:center;justify-content:center;box-shadow:0 6px 18px rgba(0,0,0,.22);transition:transform .15s,background .15s}',
    '.launcher:hover{background:var(--ekt-yellow-hover);transform:scale(1.06)}.launcher svg{width:28px;height:28px}',
    '.launcher .badge{position:absolute;top:-2px;right:-2px;min-width:20px;height:20px;padding:0 6px;border-radius:10px;background:var(--ekt-heading);color:#fff;font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;border:2px solid #fff}',
    // panel: the site is almost square, 8px is the maximum radius
    '.panel{position:fixed;right:24px;bottom:96px;z-index:2147483001;width:400px;height:640px;max-height:calc(100vh - 120px);background:var(--ekt-surface);border-radius:var(--ekt-radius-lg);border:1px solid var(--ekt-border);box-shadow:0 10px 32px rgba(11,67,102,.22);display:flex;flex-direction:column;overflow:hidden;opacity:0;transform:translateY(12px);pointer-events:none;transition:opacity .18s,transform .18s}',
    '.root.open .panel{opacity:1;transform:none;pointer-events:auto}',
    // header = the site's blue top bar: white logo + title on the left, RU/KZ, manager and close in white
    '.head{height:48px;flex:0 0 48px;display:flex;align-items:center;gap:6px;padding:0 6px 0 12px;background:var(--ekt-blue);color:#fff}',
    '.brand{flex:1;min-width:0;display:flex;flex-direction:column;justify-content:center;gap:3px}.logo{display:block;line-height:0}.logo svg{height:20px;width:95px;color:#fff}',
    '.sub{display:flex;align-items:center;gap:5px;min-width:0}.dot{width:7px;height:7px;border-radius:50%;background:#7ee2a0;flex:0 0 7px}',
    '.title{font-size:12px;line-height:14px;font-weight:700;opacity:.92;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.lang{display:flex;border:1px solid rgba(255,255,255,.6);border-radius:var(--ekt-radius);overflow:hidden;flex:0 0 auto}.lang button{padding:4px 7px;font-size:12px;line-height:1.2;font-weight:700;color:#fff;opacity:.85}.lang button:hover{opacity:1}.lang button.on{background:#fff;color:var(--ekt-blue);opacity:1}',
    '.mgr{display:flex;align-items:center;gap:4px;padding:5px 8px;border-radius:var(--ekt-radius);font-size:12px;line-height:1.2;font-weight:700;border:1px solid rgba(255,255,255,.6);color:#fff;white-space:nowrap}.mgr:hover{background:rgba(255,255,255,.14)}.mgr svg{width:14px;height:14px}',
    '.icon-btn{width:32px;height:32px;border-radius:var(--ekt-radius);display:flex;align-items:center;justify-content:center;color:#fff;flex:0 0 32px}.icon-btn:hover{background:rgba(255,255,255,.14)}.icon-btn svg{width:18px;height:18px}',
    // messages: white cards on the page background, user messages in a light tint of the blue
    '.msgs{flex:1;overflow-y:auto;padding:14px 12px;display:flex;flex-direction:column;gap:10px;background:var(--ekt-bg)}',
    '.msg{display:flex;flex-direction:column;max-width:92%}.msg.user{align-self:flex-end;align-items:flex-end}.msg.assistant{align-self:flex-start}.msg.wide{max-width:100%;width:100%}',
    '.who{font-size:11px;color:var(--ekt-muted);margin:0 2px 3px}',
    '.bubble{padding:9px 12px;border-radius:var(--ekt-radius-md);background:var(--ekt-surface);border:1px solid var(--ekt-border-light);overflow-wrap:anywhere;word-wrap:break-word}',
    '.msg.user .bubble{background:var(--ekt-blue-bg);border-color:#d3e3ec;color:var(--ekt-text)}',
    '.bubble p{margin:0}.bubble p+p,.bubble p+ul,.bubble p+ol,.bubble ul+p,.bubble ol+p{margin-top:6px}.bubble ul,.bubble ol{margin:0;padding-left:18px}.bubble li+li{margin-top:2px}',
    '.bubble code{font-family:ui-monospace,Menlo,monospace;font-size:12px;background:rgba(0,0,0,.06);padding:1px 4px;border-radius:var(--ekt-radius)}.bubble a{word-break:break-all}',
    '.files{font-size:12px;color:var(--ekt-muted);margin-top:3px}',
    '.typing{display:inline-flex;gap:4px;padding:13px 14px}.typing i{width:7px;height:7px;border-radius:50%;background:#aaa;animation:ektb 1.2s infinite}.typing i:nth-child(2){animation-delay:.2s}.typing i:nth-child(3){animation-delay:.4s}',
    '@keyframes ektb{0%,80%,100%{opacity:.3;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}',
    // quick-reply chips: the one place with a big radius, like the site's filter pills
    '.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{padding:7px 14px;border-radius:20px;border:1px solid var(--ekt-blue);color:var(--ekt-blue);font-size:13px;line-height:1.2;background:#fff}.chip:hover{background:var(--ekt-blue-bg)}',
    // product cards = catalog tiles of ekt.kz
    '.cards{display:flex;flex-direction:column;gap:8px;margin-top:8px;width:100%}',
    '.card{border:1px solid var(--ekt-border);border-radius:var(--ekt-radius-md);padding:10px;background:#fff}.card-top{display:flex;gap:10px}',
    '.thumb{width:72px;height:72px;flex:0 0 72px;border:1px solid var(--ekt-border-light);border-radius:var(--ekt-radius);background:#fff;overflow:hidden;display:flex;align-items:center;justify-content:center;color:#bbb}.thumb img{width:100%;height:100%;object-fit:contain}.thumb svg{width:28px;height:28px}',
    '.card-info{min-width:0;flex:1}.name{font-weight:700;color:var(--ekt-heading);line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}',
    '.meta{font-size:13px;color:var(--ekt-muted);margin-top:2px}.price-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:5px}.price{font-weight:700;font-size:18px;line-height:1.2}',
    '.avail{display:inline-flex;align-items:center;gap:5px;font-size:13px;font-weight:700;white-space:nowrap}.avail:before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor;flex:0 0 8px}',
    '.avail.ok{color:var(--ekt-success)}.avail.no{color:var(--ekt-danger)}.avail.order{color:var(--ekt-orange)}',
    '.stores,.certs,.reason{font-size:13px;margin-top:6px}.stores{color:var(--ekt-muted)}.certs a{display:flex;align-items:center;gap:4px;margin-top:2px}.certs svg{width:13px;height:13px;flex:0 0 13px}',
    '.reason{background:var(--ekt-bg);border-left:3px solid var(--ekt-blue);padding:6px 8px;border-radius:0 var(--ekt-radius) var(--ekt-radius) 0}',
    '.card-actions{display:flex;gap:6px;margin-top:10px}',
    // buttons: primary = the site's yellow "Купить" (white text, 3px), ghost = white with a grey border, outline = blue like "Каталог"
    '.btn{flex:1;height:36px;padding:0 12px;border-radius:var(--ekt-radius);font-size:14px;line-height:1;font-weight:700;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:4px;white-space:nowrap;transition:background .15s}',
    '.btn.primary{background:var(--ekt-yellow);color:#fff}.btn.primary:hover{background:var(--ekt-yellow-hover)}',
    '.btn.ghost{border:1px solid var(--ekt-border);background:#fff;color:var(--ekt-text)}.btn.ghost:hover{background:var(--ekt-bg)}',
    '.btn.outline{border:1px solid var(--ekt-blue);background:#fff;color:var(--ekt-blue)}.btn.outline:hover{background:var(--ekt-blue-bg)}.btn:disabled{opacity:.5;cursor:default}',
    // pending action / escalation / cart link
    '.pending{border:2px solid var(--ekt-yellow);background:#fff8e1;border-radius:var(--ekt-radius-md);padding:10px;width:100%}.pending h4{margin:0 0 4px;font-size:14px;color:var(--ekt-heading)}',
    '.pitem{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;border-top:1px dashed #e8c95c;font-size:13px}.pname{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.pqty{font-weight:700;white-space:nowrap}.pmax{font-size:11px;color:var(--ekt-muted);white-space:nowrap}.pactions{display:flex;gap:6px;margin-top:8px}',
    '.escal{border:1px solid var(--ekt-border);border-radius:var(--ekt-radius-md);padding:10px;margin-top:8px;font-size:13px;background:#fff;width:100%}.escal b{display:block;margin-bottom:4px;color:var(--ekt-heading)}.escal a{display:inline-block;margin-right:12px}',
    '.cartlink{margin-top:8px}',
    // bars: cart = dark blue of the logo, error = danger tint
    '.cartbar{display:flex;align-items:center;gap:8px;padding:9px 12px;background:var(--ekt-heading);color:#fff;font-size:13px}.cartbar svg{width:18px;height:18px;color:#fff;flex:0 0 18px}.grow{flex:1;min-width:0}.cartbar a{color:#fff;font-weight:700;text-decoration:underline;white-space:nowrap}',
    '.errbar{display:flex;align-items:center;gap:8px;padding:8px 12px;background:#f8d7da;color:#842029;font-size:13px;border-top:1px solid #f1aeb5}.errbar button{font-weight:700;text-decoration:underline;white-space:nowrap}',
    // composer
    '.composer{border-top:1px solid var(--ekt-border);padding:8px 10px;background:#fff}',
    '.attach-list{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px}.attach-list:empty{display:none}',
    '.achip{display:inline-flex;align-items:center;gap:6px;padding:4px 8px;border-radius:var(--ekt-radius);background:var(--ekt-bg);border:1px solid var(--ekt-border-light);font-size:12px;max-width:100%}.achip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.achip small{color:var(--ekt-muted);white-space:nowrap}.achip button{color:var(--ekt-muted);font-size:14px;line-height:1}',
    '.row{display:flex;align-items:flex-end;gap:6px}',
    'textarea{flex:1;min-width:0;resize:none;border:1px solid var(--ekt-border);border-radius:var(--ekt-radius-md);padding:9px 12px;font:inherit;font-size:14px;line-height:1.35;color:var(--ekt-text);background:#fff;max-height:120px;outline:none}',
    'textarea:focus{border-color:var(--ekt-blue);box-shadow:0 0 0 3px rgba(44,114,148,.15)}textarea::placeholder{color:#999}',
    '.send,.clip{width:var(--ekt-btn-h);height:var(--ekt-btn-h);flex:0 0 var(--ekt-btn-h);border-radius:var(--ekt-radius);display:flex;align-items:center;justify-content:center;transition:background .15s}',
    '.send{background:var(--ekt-yellow);color:#fff}.send:hover{background:var(--ekt-yellow-hover)}.send:disabled{opacity:.5;cursor:default}.send svg,.clip svg{width:20px;height:20px}',
    '.clip{color:var(--ekt-muted);border:1px solid var(--ekt-border)}.clip:hover{background:var(--ekt-bg);color:var(--ekt-text)}',
    // mobile: full screen panel, no launcher while open, no manager label
    '@media ' + CONFIG.mobileQuery + '{.panel{top:0;right:0;bottom:0;left:0;width:100%;height:100dvh;max-height:none;border-radius:0;border:0;padding-top:env(safe-area-inset-top)}',
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
  // The real ekt.kz logo (docs/design/logo.svg, 176x37) recoloured to currentColor (white on the blue header).
  var LOGO = '<svg viewBox="0 0 176 37" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M1.9129,15.78 L0.6379,15.78 L0.6379,3.0662 L7.0126,3.0662 L7.0126,4.3057 L1.9129,4.3057 L1.9129,15.78 Z M8.8334,3.0662 L11.3684,3.0662 C12.8204,3.0662 13.8032,3.1281 14.3108,3.255 C15.0368,3.4351 15.63,3.801 16.0904,4.3588 C16.5537,4.9136 16.7839,5.6131 16.7839,6.4542 C16.7839,7.3012 16.5596,7.9976 16.1081,8.5495 C15.6595,9.0984 15.0397,9.4673 14.2518,9.6592 C13.6733,9.7979 12.5961,9.8658 11.0172,9.8658 L10.1083,9.8658 L10.1083,15.78 L8.8334,15.78 L8.8334,3.0662 Z M10.1083,4.3057 L10.1083,8.6263 L12.2626,8.6528 C13.1333,8.6528 13.7707,8.5731 14.175,8.4138 C14.5794,8.2544 14.8981,8.0006 15.1283,7.6464 C15.3585,7.2923 15.4736,6.8939 15.4736,6.4571 C15.4736,6.0321 15.3585,5.6396 15.1283,5.2855 C14.8981,4.9313 14.5912,4.6805 14.2134,4.53 C13.8356,4.3795 13.2188,4.3057 12.3571,4.3057 L10.1083,4.3057 Z M18.3481,3.1016 L19.7735,3.1016 L23.6337,11.7368 L27.1544,3.0662 L28.5474,3.0662 L23.7164,14.4903 C23.2678,15.5616 22.4709,16.0987 21.3229,16.0987 C20.8625,16.0987 20.4287,16.0043 20.0185,15.8184 C19.6083,15.6295 19.0918,15.1868 18.4632,14.4903 L19.3899,13.7289 C20.0273,14.3575 20.4523,14.7146 20.6648,14.8002 C20.8802,14.8858 21.0957,14.93 21.3141,14.93 C21.6475,14.93 21.9368,14.8415 22.1817,14.6644 C22.4267,14.4844 22.6952,14.0417 22.9874,13.3305 L18.3481,3.1016 L18.3481,3.1016 Z M30.7342,3.0662 L39.765,3.0662 L39.765,15.78 L38.49,15.78 L38.49,4.2703 L32.0092,4.2703 L32.0092,15.78 L30.7342,15.78 L30.7342,3.0662 Z M42.8311,3.0662 L51.8619,3.0662 L51.8619,15.78 L50.5869,15.78 L50.5869,4.2703 L44.1061,4.2703 L44.1061,15.78 L42.8311,15.78 L42.8311,3.0662 Z M60.1517,3.0662 L66.0836,15.78 L64.7172,15.78 L62.731,11.6011 L57.2212,11.6011 L55.2469,15.78 L53.8303,15.78 L59.8329,3.0662 L60.1517,3.0662 Z M59.9923,5.7341 L57.8056,10.3616 L62.1703,10.3616 L59.9923,5.7341 Z M72.9452,3.0662 L74.2378,3.0662 L74.2378,7.8353 L79.3345,3.0662 L81.0462,3.0662 L74.9195,8.7679 L81.5243,15.78 L79.8274,15.78 L74.2378,9.8422 L74.2378,15.78 L72.9452,15.78 L72.9452,3.0662 L72.9452,3.0662 Z M89.469,2.7474 C91.3931,2.7474 93.0045,3.3908 94.3001,4.6746 C95.5957,5.9584 96.2449,7.5402 96.2449,9.4172 C96.2449,11.2794 95.5986,12.8583 94.306,14.1539 C93.0104,15.4495 91.4344,16.0987 89.5723,16.0987 C87.6895,16.0987 86.0987,15.4524 84.8032,14.1627 C83.5076,12.8731 82.8583,11.3119 82.8583,9.4792 C82.8583,8.2573 83.1534,7.127 83.7437,6.0823 C84.3339,5.0405 85.1396,4.223 86.1607,3.6328 C87.1789,3.0425 88.2826,2.7474 89.469,2.7474 L89.469,2.7474 Z M89.5251,3.9869 C88.5866,3.9869 87.6954,4.2319 86.8513,4.7218 C86.0102,5.2117 85.3521,5.8698 84.8769,6.6991 C84.4047,7.5313 84.1686,8.4551 84.1686,9.4762 C84.1686,10.9843 84.691,12.2592 85.7387,13.298 C86.7864,14.3398 88.0495,14.8592 89.5251,14.8592 C90.5137,14.8592 91.4256,14.6202 92.2637,14.1421 C93.1048,13.664 93.76,13.0088 94.2292,12.1795 C94.6985,11.3502 94.9346,10.4294 94.9346,9.4142 C94.9346,8.4049 94.6985,7.493 94.2292,6.6784 C93.76,5.8639 93.096,5.2117 92.2431,4.7218 C91.3902,4.2319 90.4842,3.9869 89.5251,3.9869 Z M98.2488,15.78 L100.0107,3.0662 L100.2733,3.0662 L105.4468,13.4958 L110.5877,3.0662 L110.7796,3.0662 L112.6093,15.78 L111.3787,15.78 L110.1037,6.6784 L105.6003,15.78 L105.2668,15.78 L100.719,6.6076 L99.5001,15.78 L98.2488,15.78 Z M115.0529,3.0662 L124.0836,3.0662 L124.0836,15.78 L122.8086,15.78 L122.8086,4.2703 L116.3279,4.2703 L116.3279,15.78 L115.0529,15.78 L115.0529,3.0662 Z M132.3735,3.0662 L138.3054,15.78 L136.939,15.78 L134.9528,11.6011 L129.4429,11.6011 L127.4686,15.78 L126.052,15.78 L132.0547,3.0662 L132.3735,3.0662 Z M132.2141,5.7341 L130.0273,10.3616 L134.3921,10.3616 L132.2141,5.7341 Z M140.2472,3.0662 L141.5222,3.0662 L141.5222,8.3961 L148.003,8.3961 L148.003,3.0662 L149.278,3.0662 L149.278,15.78 L148.003,15.78 L148.003,9.6356 L141.5222,9.6356 L141.5222,15.78 L140.2472,15.78 L140.2472,3.0662 Z M162.3842,15.78 L161.0561,15.78 L161.0561,6.0115 L152.6659,15.78 L152.3796,15.78 L152.3796,3.0662 L153.6546,3.0662 L153.6546,12.9793 L162.0979,3.0662 L162.3842,3.0662 L162.3842,15.78 Z M175.4816,15.78 L174.1535,15.78 L174.1535,6.0115 L165.7633,15.78 L165.477,15.78 L165.477,3.0662 L166.752,3.0662 L166.752,12.9793 L175.1953,3.0662 L175.4816,3.0662 L175.4816,15.78 Z M167.4248,0.0028 L168.5581,0.0028 C168.7558,0.3038 169.0185,0.5281 169.349,0.6786 C169.6766,0.8291 170.0721,0.9059 170.5266,0.9059 C170.9898,0.9059 171.3646,0.838 171.6479,0.7052 C171.9313,0.5724 172.1998,0.3392 172.4536,0.0028 L173.5515,0.0028 C173.4246,0.5222 173.0999,0.956 172.5805,1.3043 C172.0582,1.6525 171.3705,1.8266 170.5177,1.8266 C169.6707,1.8266 168.9801,1.6555 168.446,1.3131 C167.9088,0.9708 167.5695,0.534 167.4248,0.0028 L167.4248,0.0028 Z"/>' +
    '<path fill="currentColor" d="M1.7348,32.3359 L0.0549,34.1003 C1.0701,35.1034 2.0188,35.7712 2.9041,36.1005 C3.7924,36.4298 4.726,36.596 5.714,36.596 C7.7866,36.596 9.4544,35.9434 10.7204,34.6351 C11.9864,33.3299 12.6208,31.6983 12.6208,29.7405 C12.6208,27.698 11.9259,26.0544 10.5391,24.8036 C9.1523,23.5527 7.5298,22.9273 5.6777,22.9273 C4.6263,22.9273 3.593,23.1539 2.5778,23.6101 C1.5596,24.0633 0.7015,24.7099 0.0005,25.5468 L1.7348,27.1874 C2.8829,25.9426 4.2123,25.3202 5.714,25.3202 C6.8107,25.3202 7.7715,25.6677 8.5933,26.3626 C9.4182,27.0545 9.8865,27.8491 10.0073,28.7465 L5.0251,28.7465 L5.0251,31.0125 L10.0073,31.0125 C9.669,32.1274 9.1009,32.9401 8.2972,33.4447 C7.4966,33.9493 6.6204,34.2031 5.6717,34.2031 C4.2516,34.2031 2.9373,33.5807 1.7348,32.3359 L1.7348,32.3359 Z M23.3891,36.2697 L19.7785,26.7282 L16.1136,36.2697 L13.485,36.2697 L18.5126,23.2536 L21.0264,23.2536 L26.0389,36.2697 L23.3891,36.2697 Z M27.5525,23.2536 L34.6588,23.2536 L34.6588,25.6828 L30.018,25.6828 L30.018,28.0213 L34.6588,28.0213 L34.6588,30.4143 L30.018,30.4143 L30.018,33.8405 L34.6588,33.8405 L34.6588,36.2697 L27.5525,36.2697 L27.5525,23.2536 Z M36.9792,23.2536 L39.481,23.2536 L39.481,27.7554 L43.0039,23.2536 L45.992,23.2536 L41.4449,29.0637 L46.4332,36.2697 L43.4934,36.2697 L39.481,30.4717 L39.481,36.2697 L36.9792,36.2697 L36.9792,23.2536 L36.9792,23.2536 Z M46.9347,23.2536 L54.1317,23.2536 L54.1317,25.7009 L51.7569,25.7009 L51.7569,36.2697 L49.237,36.2697 L49.237,25.7009 L46.9347,25.7009 L46.9347,23.2536 L46.9347,23.2536 Z M55.7995,23.2536 L58.4311,23.2536 C59.8542,23.2536 60.8784,23.3835 61.5069,23.6464 C62.1353,23.9092 62.6339,24.3352 62.9964,24.9214 C63.359,25.5075 63.5403,26.2115 63.5403,27.0333 C63.5403,27.9428 63.3016,28.6981 62.8242,29.2994 C62.3499,29.9006 61.7033,30.3206 60.8845,30.5563 C60.4041,30.6922 59.5309,30.7587 58.2649,30.7587 L58.2649,36.2697 L55.7995,36.2697 L55.7995,23.2536 L55.7995,23.2536 Z M58.2649,28.3476 L59.0596,28.3476 C59.685,28.3476 60.117,28.3023 60.3618,28.2147 C60.6065,28.1241 60.7969,27.979 60.9389,27.7736 C61.0778,27.5681 61.1473,27.3204 61.1473,27.0273 C61.1473,26.5227 60.951,26.1541 60.5582,25.9245 C60.2742,25.7523 59.7424,25.6646 58.9689,25.6646 L58.2649,25.6646 L58.2649,28.3476 L58.2649,28.3476 Z M72.1058,22.9273 C73.9459,22.9273 75.5291,23.595 76.8524,24.9274 C78.1788,26.2599 78.8405,27.8854 78.8405,29.8009 C78.8405,31.7014 78.1879,33.3087 76.8796,34.623 C75.5744,35.9373 73.9882,36.596 72.124,36.596 C70.1691,36.596 68.5467,35.9192 67.2565,34.5687 C65.9634,33.2181 65.3168,31.6137 65.3168,29.7556 C65.3168,28.5138 65.6189,27.3687 66.2202,26.3233 C66.8215,25.2809 67.6493,24.4531 68.7008,23.8428 C69.7552,23.2324 70.8913,22.9273 72.1058,22.9273 L72.1058,22.9273 Z M72.0696,25.3565 C70.8701,25.3565 69.861,25.7764 69.0452,26.6134 C68.2264,27.4503 67.8185,28.5138 67.8185,29.807 C67.8185,31.2451 68.3321,32.3842 69.3624,33.2211 C70.1631,33.8768 71.0786,34.2031 72.1149,34.2031 C73.2842,34.2031 74.2812,33.7771 75.1031,32.9281 C75.9279,32.079 76.3388,31.0336 76.3388,29.7888 C76.3388,28.5501 75.9249,27.5016 75.094,26.6436 C74.2661,25.7855 73.257,25.3565 72.0696,25.3565 L72.0696,25.3565 Z M81.2122,23.2536 L83.714,23.2536 L83.714,27.7554 L87.2369,23.2536 L90.225,23.2536 L85.6779,29.0637 L90.6662,36.2697 L87.7264,36.2697 L83.714,30.4717 L83.714,36.2697 L81.2122,36.2697 L81.2122,23.2536 L81.2122,23.2536 Z M98.5731,22.9273 C100.4132,22.9273 101.9964,23.595 103.3197,24.9274 C104.6461,26.2599 105.3078,27.8854 105.3078,29.8009 C105.3078,31.7014 104.6552,33.3087 103.3469,34.623 C102.0417,35.9373 100.4555,36.596 98.5913,36.596 C96.6364,36.596 95.014,35.9192 93.7238,34.5687 C92.4307,33.2181 91.7841,31.6137 91.7841,29.7556 C91.7841,28.5138 92.0862,27.3687 92.6875,26.3233 C93.2888,25.2809 94.1166,24.4531 95.1681,23.8428 C96.2225,23.2324 97.3586,22.9273 98.5731,22.9273 L98.5731,22.9273 Z M98.5369,25.3565 C97.3374,25.3565 96.3283,25.7764 95.5125,26.6134 C94.6937,27.4503 94.2858,28.5138 94.2858,29.807 C94.2858,31.2451 94.7994,32.3842 95.8297,33.2211 C96.6304,33.8768 97.5459,34.2031 98.5822,34.2031 C99.7515,34.2031 100.7485,33.7771 101.5704,32.9281 C102.3952,32.079 102.8061,31.0336 102.8061,29.7888 C102.8061,28.5501 102.3922,27.5016 101.5613,26.6436 C100.7334,25.7855 99.7243,25.3565 98.5369,25.3565 L98.5369,25.3565 Z M108.9093,23.2536 L111.3113,23.2536 L114.3417,32.3328 L117.3873,23.2536 L119.7832,23.2536 L121.9646,36.2697 L119.5747,36.2697 L118.1849,28.0485 L115.4294,36.2697 L113.2419,36.2697 L110.4985,28.0485 L109.0815,36.2697 L106.6644,36.2697 L108.9093,23.2536 L108.9093,23.2536 Z M123.9557,23.2536 L133.328,23.2536 L133.328,36.2697 L130.8263,36.2697 L130.8263,25.574 L126.4755,25.574 L126.4755,36.2697 L123.9557,36.2697 L123.9557,23.2536 Z M144.7368,36.2697 L141.1262,26.7282 L137.4613,36.2697 L134.8327,36.2697 L139.8603,23.2536 L142.3741,23.2536 L147.3866,36.2697 L144.7368,36.2697 L144.7368,36.2697 Z M148.9002,23.2536 L156.0065,23.2536 L156.0065,25.6828 L151.3657,25.6828 L151.3657,28.0213 L156.0065,28.0213 L156.0065,30.4143 L151.3657,30.4143 L151.3657,33.8405 L156.0065,33.8405 L156.0065,36.2697 L148.9002,36.2697 L148.9002,23.2536 Z M158.3269,23.2536 L160.8287,23.2536 L160.8287,27.7554 L164.3516,23.2536 L167.3397,23.2536 L162.7926,29.0637 L167.7809,36.2697 L164.8411,36.2697 L160.8287,30.4717 L160.8287,36.2697 L158.3269,36.2697 L158.3269,23.2536 L158.3269,23.2536 Z M168.2824,23.2536 L175.4794,23.2536 L175.4794,25.7009 L173.1046,25.7009 L173.1046,36.2697 L170.5847,36.2697 L170.5847,25.7009 L168.2824,25.7009 L168.2824,23.2536 Z"/></svg>';

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

  /** Availability: dot + text, green (in stock, N шт) / red (none) / orange (stock unknown -> "Под заказ"). */
  function stockBadge(p) {
    var unknown = p.stock_status === 'unknown' || (p.in_stock == null && p.quantity == null);
    if (unknown) return el('span', { class: 'avail order' }, [t().onOrder]);
    var qty = Number(p.quantity || 0);
    if (p.in_stock || qty > 0) return el('span', { class: 'avail ok' }, [qty > 0 ? fill(t().inStock, { n: qty }) : t().inStock.split(':')[0]]);
    return el('span', { class: 'avail no' }, [t().outOfStock]);
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
        safeUrl(p.url) ? el('a', { class: 'btn outline', href: safeUrl(p.url), target: '_blank', rel: 'noopener noreferrer' }, [t().onSite]) : null
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

  /** Add the PT Sans <link> to the host document once (skipped when the page already loads PT Sans, e.g. ekt.kz itself). */
  function loadFont() {
    if (document.getElementById('ekt-ai-font') || document.querySelector('link[href*="family=PT+Sans"]')) return;
    var head = document.head || document.documentElement;
    head.appendChild(el('link', { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: '' }));
    head.appendChild(el('link', { id: 'ekt-ai-font', rel: 'stylesheet', href: CONFIG.fontUrl }));
  }

  /** Build the static shell once inside the shadow root. */
  function mount() {
    loadFont();
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
    var brand = el('div', { class: 'brand' }, [
      el('span', { class: 'logo', html: LOGO }),
      el('span', { class: 'sub' }, [el('span', { class: 'dot' }), ui.title])
    ]);
    ui.langBtns = ['ru', 'kk'].map(function (l) {
      return el('button', { type: 'button', 'data-lang': l, onclick: function () { setLang(l); } }, [l === 'ru' ? 'RU' : 'KZ']);
    });
    ui.mgrLabel = el('span');
    ui.mgr = el('button', { class: 'mgr', type: 'button', html: ICON.user, onclick: function () { sendMessage(t().managerMsg); } });
    ui.mgr.appendChild(ui.mgrLabel);
    ui.closeBtn = el('button', { class: 'icon-btn', type: 'button', html: ICON.close, onclick: close });
    var head = el('div', { class: 'head' }, [brand, el('div', { class: 'lang' }, ui.langBtns), ui.mgr, ui.closeBtn]);

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

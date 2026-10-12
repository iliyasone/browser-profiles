// Saved logins on this site (https pages only, subdomains included). A panel
// pinned to the right edge lists every login: click the username to fill the
// sign-in form, the password to copy it, and the authenticator code to type it
// into the code field (or copy it when no field is clearly the one).
// Unasked, a sign-in form is filled only on the login's own host, with one
// login there, when it is the only password field on the page and its username
// is empty or already this login's; never submitted. Without a field that
// names itself the username, nothing is filled unasked. Another subdomain may
// be a third party's page, so there the panel has to be clicked.
// SAVED_LOGINS comes from logins.js, written per profile.

const PERIOD_MS = 30000;
const COLLAPSED_KEY = "saved-logins-collapsed";

const logins = globalThis.SAVED_LOGINS.filter(
  (login) =>
    location.hostname === login.host ||
    location.hostname.endsWith("." + login.host),
);

function visible(element) {
  return element.getClientRects().length > 0;
}

// The prototype's setter writes the DOM value underneath any framework
// wrapper, so a React-style form sees the following input event as a change.
function setValue(input, value) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  ).set;
  input.focus();
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

// The visible password field and its username field: among the text fields
// before it (in its form, if it has one), the one that says it is a login.
// With `guess`, else the nearest one — fine when the operator asked for the
// fill, not when nobody did: on a password-confirm step the nearest field may
// be an amount or a wallet address.
function loginFields(guess) {
  const password = [
    ...document.querySelectorAll('input[type="password"]'),
  ].find(visible);
  if (!password) return null;
  const before = [
    ...(password.form ?? document).querySelectorAll(
      'input:not([type]), input[type="text"], input[type="email"], input[type="tel"]',
    ),
  ].filter(
    (input) =>
      visible(input) &&
      input.compareDocumentPosition(password) & Node.DOCUMENT_POSITION_FOLLOWING,
  );
  const named = before.findLast(
    (input) =>
      input.autocomplete === "username" ||
      input.autocomplete === "email" ||
      /user|login|e?mail/i.test(input.name + " " + input.id),
  );
  return {
    username: named ?? (guess ? before.at(-1) : null) ?? null,
    password,
  };
}

// A row of six one-digit boxes, or a single code field. The row comes first:
// such rows often mark only their first box as the code field, and six digits
// typed into it would end up as one. Of single fields, one marked
// `one-time-code` wins; otherwise its name must say so and not be one of the
// other codes a page asks for. More than one candidate is a guess: none.
function codeFields() {
  const inputs = [
    ...document.querySelectorAll(
      'input:not([type]), input[type="text"], input[type="tel"], input[type="number"]',
    ),
  ].filter(visible);
  const boxes = inputs.filter((input) => input.maxLength === 1);
  if (boxes.length === 6) return boxes;
  const marked = inputs.filter((input) => input.autocomplete === "one-time-code");
  if (marked.length === 1) return marked;
  const named = inputs.filter((input) => {
    const text = [input.name, input.id, input.placeholder, input.ariaLabel].join(" ");
    return (
      /otp|2fa|totp|verif|code/i.test(text) &&
      !/captcha|promo|bonus|coupon|voucher|referr|invite|post|zip|country|phone|sms|mail/i.test(
        text,
      )
    );
  });
  return named.length === 1 ? named : [];
}

function fillLogin(login, fields) {
  if (fields.username) setValue(fields.username, login.username);
  setValue(fields.password, login.password);
}

// The form to fill unasked, or null when filling it could misfire: a sign-up
// or change-password page, a password-confirm step with no username field, or
// a username typed for another account.
function autofillFields(login) {
  const own = location.hostname.replace(/^www\./, "") === login.host;
  const passwords = [
    ...document.querySelectorAll('input[type="password"]'),
  ].filter(visible);
  if (!own || passwords.length !== 1) return null;
  if (passwords[0].autocomplete === "new-password") return null;
  const fields = loginFields(false);
  if (!fields.username) return null;
  const typed = fields.username.value;
  return typed && typed !== login.username ? null : fields;
}

// "copied" or "copy failed": a copy needs a fresh click, and a panel that
// claims a copy that did not happen leaves the old clipboard (a password) to
// be pasted as the code.
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    return "copied";
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    const done = document.execCommand("copy");
    area.remove();
    return done ? "copied" : "copy failed";
  }
}

function base32Bytes(secret) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of secret.toUpperCase().replace(/[\s=]/g, "")) {
    bits += alphabet.indexOf(char).toString(2).padStart(5, "0");
  }
  const bytes = new Uint8Array(Math.floor(bits.length / 8));
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(bits.slice(i * 8, i * 8 + 8), 2);
  }
  return bytes;
}

// RFC 6238 with the authenticator-app defaults: SHA-1, 30 s, 6 digits.
async function totp(secret, now) {
  const key = await crypto.subtle.importKey(
    "raw",
    base32Bytes(secret),
    { name: "HMAC", hash: "SHA-1" },
    false,
    ["sign"],
  );
  const counter = new DataView(new ArrayBuffer(8));
  counter.setBigUint64(0, BigInt(Math.floor(now / PERIOD_MS)));
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, counter));
  const offset = mac[19] & 0xf;
  const value =
    (((mac[offset] & 0x7f) << 24) |
      (mac[offset + 1] << 16) |
      (mac[offset + 2] << 8) |
      mac[offset + 3]) %
    1000000;
  return value.toString().padStart(6, "0");
}

// Types the current code into the code field, else copies it. The operator
// still has to press submit through the remote screen, and a code that
// expires on the way counts as a wrong one upstream: with under 10 s left, a
// fill waits for the next code; a copy cannot wait (it needs the click), so it
// asks for another click instead.
async function useCode(secret, flash) {
  const left = PERIOD_MS - (Date.now() % PERIOD_MS);
  const seconds = Math.ceil(left / 1000);
  const fields = codeFields();
  if (fields.length === 0) {
    if (left < 10000) flash(`new code in ${seconds} s, click again then`);
    else flash(await copy(await totp(secret, Date.now())));
    return;
  }
  if (left < 10000) {
    flash(`next code in ${seconds} s…`, left + 100);
    setTimeout(() => useCode(secret, flash), left + 100);
    return;
  }
  const code = await totp(secret, Date.now());
  if (fields.length === 1) setValue(fields[0], code);
  else fields.forEach((field, i) => setValue(field, code[i]));
  flash("filled");
}

const STYLE = `
  :host { all: initial; }
  .panel { position: fixed; top: 30%; right: 0; z-index: 2147483647;
    font: 13px/1.35 system-ui, sans-serif; color: #e8e8e8; background: #1f2329;
    border: 1px solid #3a404a; border-right: none; border-radius: 8px 0 0 8px;
    box-shadow: 0 4px 16px rgba(0,0,0,.35); width: 210px; }
  .panel.collapsed { width: auto; }
  .head { display: flex; align-items: center; justify-content: space-between;
    padding: 6px 10px; cursor: pointer; color: #9aa4b2; user-select: none; }
  .collapsed .head { padding: 8px 6px; }
  .collapsed .body, .collapsed .title { display: none; }
  .login { padding: 8px 10px; border-top: 1px solid #3a404a; }
  .row { display: flex; justify-content: space-between; align-items: center;
    gap: 6px; padding: 3px 6px; margin: 0 -6px; border-radius: 4px;
    cursor: pointer; }
  .row:hover { background: #2c323b; }
  .label { color: #9aa4b2; font-size: 11px; }
  .value { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .code { font: 600 22px/1.2 ui-monospace, monospace; letter-spacing: 2px; }
  .bar { height: 3px; background: #3a404a; border-radius: 2px; margin-top: 2px; }
  .bar > div { height: 100%; background: #4f9dff; border-radius: 2px; }
  .flash { color: #7fd17f; font-size: 11px; min-height: 14px; }
`;

function render() {
  const host = document.createElement("div");
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `<style>${STYLE}</style>`;
  const panel = document.createElement("div");
  panel.className = "panel";
  // Extension storage, not the site's: the page should not find our traces.
  chrome.storage.local.get(COLLAPSED_KEY).then((stored) => {
    panel.classList.toggle("collapsed", stored[COLLAPSED_KEY] === true);
  });
  const head = document.createElement("div");
  head.className = "head";
  head.innerHTML = `<span>🔑</span><span class="title">saved logins</span>`;
  head.onclick = () => {
    const collapsed = panel.classList.toggle("collapsed");
    chrome.storage.local.set({ [COLLAPSED_KEY]: collapsed });
  };
  const body = document.createElement("div");
  body.className = "body";
  panel.append(head, body);

  const ticks = [];
  for (const login of logins) {
    const box = document.createElement("div");
    box.className = "login";
    const flashLine = document.createElement("div");
    flashLine.className = "flash";
    let flashTimer;
    const flash = (text, holdMs = 1500) => {
      flashLine.textContent = text;
      clearTimeout(flashTimer);
      flashTimer = setTimeout(() => (flashLine.textContent = ""), holdMs);
    };
    const row = (label, value, onClick) => {
      const element = document.createElement("div");
      element.className = "row";
      element.title = label;
      element.innerHTML = `<span class="label"></span><span class="value"></span>`;
      element.querySelector(".label").textContent = label;
      element.querySelector(".value").textContent = value;
      element.onclick = onClick;
      box.append(element);
      return element;
    };
    row("login", login.username, async () => {
      const fields = loginFields(true);
      if (fields) {
        fillLogin(login, fields);
        flash("filled");
      } else {
        flash(await copy(login.username));
      }
    });
    row("password", "••••••••", async () => {
      flash(await copy(login.password));
    });
    if (login.totp_secret) {
      const code = row("code", "", () => useCode(login.totp_secret, flash));
      const value = code.querySelector(".value");
      value.classList.add("code");
      const bar = document.createElement("div");
      bar.className = "bar";
      bar.innerHTML = "<div></div>";
      box.append(bar);
      ticks.push(async () => {
        const now = Date.now();
        value.textContent = await totp(login.totp_secret, now);
        bar.firstChild.style.width = `${(100 * (PERIOD_MS - (now % PERIOD_MS))) / PERIOD_MS}%`;
      });
    }
    box.append(flashLine);
    body.append(box);
  }
  root.append(panel);
  document.documentElement.append(host);
  return ticks;
}

if (logins.length > 0) {
  const ticks = render();
  const filled = new WeakSet();
  const tick = () => {
    ticks.forEach((update) => update());
    if (logins.length === 1) {
      const fields = autofillFields(logins[0]);
      if (fields && !filled.has(fields.password) && !fields.password.value) {
        filled.add(fields.password);
        fillLogin(logins[0], fields);
      }
    }
  };
  tick();
  setInterval(tick, 1000);
}

// Saved logins on this site. With one login for the site, an empty sign-in
// form is filled as soon as it shows up (never submitted, never overwritten
// once filled). A panel pinned to the right edge lists every login: click the
// username to fill the form, the password to copy it, and the authenticator
// code to type it into the code field (or copy it when there is none).
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

// The visible password field and the text field right before it.
function loginFields() {
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
  return { username: before.at(-1) ?? null, password };
}

// A single code field, or a row of six one-digit boxes.
function codeFields() {
  const inputs = [...document.querySelectorAll("input")].filter(
    (input) =>
      visible(input) && input.type !== "password" && input.type !== "hidden",
  );
  const single = inputs.find(
    (input) =>
      input.autocomplete === "one-time-code" ||
      /otp|2fa|totp|verif|code/i.test(
        [input.name, input.id, input.placeholder, input.ariaLabel].join(" "),
      ),
  );
  if (single) return [single];
  const boxes = inputs.filter((input) => input.maxLength === 1);
  return boxes.length === 6 ? boxes : [];
}

function fillLogin(login) {
  const fields = loginFields();
  if (!fields) return false;
  if (fields.username) setValue(fields.username, login.username);
  setValue(fields.password, login.password);
  return true;
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
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

// Types the current code into the code field. A code with under 3 s left
// would likely expire on the way, so the next one is waited for instead.
async function useCode(secret, flash) {
  const left = PERIOD_MS - (Date.now() % PERIOD_MS);
  if (left < 3000) {
    flash("wait…");
    setTimeout(() => useCode(secret, flash), left + 100);
    return;
  }
  const code = await totp(secret, Date.now());
  const fields = codeFields();
  if (fields.length === 1) {
    setValue(fields[0], code);
    flash("filled");
  } else if (fields.length === 6) {
    fields.forEach((field, i) => setValue(field, code[i]));
    flash("filled");
  } else {
    await copy(code);
    flash("copied");
  }
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
  panel.classList.toggle(
    "collapsed",
    localStorage.getItem(COLLAPSED_KEY) === "1",
  );
  const head = document.createElement("div");
  head.className = "head";
  head.innerHTML = `<span>🔑</span><span class="title">saved logins</span>`;
  head.onclick = () => {
    const collapsed = panel.classList.toggle("collapsed");
    localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
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
    const flash = (text) => {
      flashLine.textContent = text;
      clearTimeout(flashTimer);
      flashTimer = setTimeout(() => (flashLine.textContent = ""), 1500);
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
      if (fillLogin(login)) flash("filled");
      else {
        await copy(login.username);
        flash("copied");
      }
    });
    row("password", "••••••••", async () => {
      await copy(login.password);
      flash("copied");
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
      const fields = loginFields();
      if (fields && !filled.has(fields.password) && !fields.password.value) {
        filled.add(fields.password);
        fillLogin(logins[0]);
      }
    }
  };
  tick();
  setInterval(tick, 1000);
}

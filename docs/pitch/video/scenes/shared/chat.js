/* Chat playback shared by the phone scenes: reveal messages at set times, auto-scroll, "typing…" before bot replies. */
function hfChatMeasure(phone) {
  const area = phone.querySelector(".ph-msgs");
  const inner = phone.querySelector(".ph-inner");
  const viewH = area.clientHeight || 620;
  const msgs = Array.from(inner.children);
  const shifts = msgs.map((m) => Math.max(0, m.offsetTop + m.offsetHeight + 20 - viewH));
  return { inner, msgs, shifts };
}

function hfPlayChat(tl, phone, times) {
  const { inner, msgs, shifts } = hfChatMeasure(phone);
  const typing = phone.querySelector(".ph-sub .typing");
  const online = phone.querySelector(".ph-sub .online");
  let prev = 0;
  msgs.forEach((m, i) => {
    const t = times[i];
    if (t == null) return;
    if (m.classList.contains("bot") && typing) {
      const ts = Math.max(0, t - 0.85);
      tl.fromTo(typing, { opacity: 0 }, { opacity: 1, duration: 0.15 }, ts);
      tl.fromTo(online, { opacity: 1 }, { opacity: 0, duration: 0.15 }, ts);
      tl.fromTo(typing, { opacity: 1 }, { opacity: 0, duration: 0.15, immediateRender: false }, t);
      tl.fromTo(online, { opacity: 0 }, { opacity: 1, duration: 0.15, immediateRender: false }, t);
    }
    tl.fromTo(m, { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.35, ease: "power2.out" }, t);
    if (shifts[i] > prev) {
      tl.fromTo(inner, { y: -prev }, { y: -shifts[i], duration: 0.4, ease: "power2.inOut", immediateRender: false }, t - 0.05);
      prev = shifts[i];
    }
  });
}

function hfFinalChat(phone) {
  const { inner, shifts } = hfChatMeasure(phone);
  gsap.set(inner, { y: -shifts[shifts.length - 1] });
}

function hfCount(tl, el, to, at, dur, fmt) {
  const o = { v: 0 };
  el.textContent = fmt(0);
  tl.to(o, { v: to, duration: dur, ease: "power2.out", onUpdate: () => { el.textContent = fmt(o.v); } }, at);
}

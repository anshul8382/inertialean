/**
 * Live India Standard Time clock (Asia/Kolkata), independent of device timezone.
 */
(function () {
  var el = document.getElementById('inertia-ist-clock');
  if (!el) return;
  var timeEl = el.querySelector('.inertia-ist-clock__time');
  if (!timeEl) return;

  var fmt = new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    weekday: 'short',
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });

  function tick() {
    var now = new Date();
    timeEl.textContent = fmt.format(now);
    timeEl.setAttribute('datetime', now.toISOString());
  }

  tick();
  setInterval(tick, 1000);
})();

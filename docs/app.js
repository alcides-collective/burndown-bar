// Copy the install command to the clipboard. Decodes HTML entities so what
// lands on the clipboard is runnable, not escaped markup.
document.querySelectorAll(".copy").forEach(function (btn) {
  btn.addEventListener("click", function () {
    var pre = document.getElementById(btn.dataset.target);
    if (!pre) return;
    var text = pre.textContent;
    navigator.clipboard.writeText(text).then(function () {
      var original = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(function () { btn.textContent = original; }, 1500);
    });
  });
});

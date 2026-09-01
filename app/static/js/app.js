// Vanilla JS only. No frameworks, no analytics.
// Never logs message text, tokens, or clipboard contents.
(function () {
  "use strict";

  function setupCharCounter() {
    var textarea = document.getElementById("text");
    var counter = document.getElementById("char-counter");
    if (!textarea || !counter) return;
    var max = parseInt(counter.getAttribute("data-max"), 10) || 2000;

    function update() {
      var len = textarea.value.length;
      counter.textContent = len + " / " + max;
    }

    textarea.addEventListener("input", update);
    update();
  }

  function fallbackCopy(text) {
    var temp = document.createElement("textarea");
    temp.value = text;
    temp.setAttribute("readonly", "");
    temp.style.position = "absolute";
    temp.style.left = "-9999px";
    document.body.appendChild(temp);
    temp.select();
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (err) {
      ok = false;
    }
    document.body.removeChild(temp);
    return ok;
  }

  function setupCopyButtons() {
    var buttons = document.querySelectorAll("[data-copy-target]");
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var targetId = btn.getAttribute("data-copy-target");
        var target = document.getElementById(targetId);
        var feedback = document.getElementById("copy-feedback");
        if (!target) return;
        var value = target.value !== undefined ? target.value : target.textContent;

        function showFeedback(success) {
          if (feedback) {
            feedback.textContent = success ? "Copied." : "Could not copy. Please select and copy manually.";
          }
        }

        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard
            .writeText(value)
            .then(function () {
              showFeedback(true);
            })
            .catch(function () {
              showFeedback(fallbackCopy(value));
            });
        } else {
          showFeedback(fallbackCopy(value));
        }
      });
    });
  }

  function setupShareButton() {
    var shareBtn = document.getElementById("share-text-btn");
    var textarea = document.getElementById("received-text");
    if (!shareBtn || !textarea) return;
    if (navigator.share) {
      shareBtn.hidden = false;
      shareBtn.addEventListener("click", function () {
        navigator.share({ text: textarea.value }).catch(function () {
          // User cancelled or share failed; nothing sensitive to log.
        });
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    setupCharCounter();
    setupCopyButtons();
    setupShareButton();
  });
})();

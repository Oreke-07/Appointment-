document.addEventListener("DOMContentLoaded", () => {
  const flashMessages = document.querySelectorAll(".alert");
  flashMessages.forEach((message) => {
    setTimeout(() => {
      message.style.opacity = "0";
      message.style.transition = "opacity 0.5s ease";
    }, 3000);
  });

  const forms = document.querySelectorAll("form");
  forms.forEach((form) => {
    form.addEventListener("submit", () => {
      const submitButton = form.querySelector('button[type="submit"]');
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "Processing...";
      }
    });
  });
});

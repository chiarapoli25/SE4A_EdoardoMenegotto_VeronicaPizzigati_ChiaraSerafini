const checkStatusButton = document.querySelector("#check-status");
const systemStatus = document.querySelector("#system-status");

checkStatusButton.addEventListener("click", () => {
  systemStatus.textContent = "Dashboard ready";
});

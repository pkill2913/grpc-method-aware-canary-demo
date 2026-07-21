const state = {
  theme: "blue",
  method: "GetUser",
  name: "demo-user",
  status: "active",
  userServiceVersion: "v1",
  message: "Hello demo-user with blue theme from v1",
  points: null,
  versions: {
    userService: "v1",
  },
};

const themeColors = {
  blue: "#2563eb",
  purple: "#7c3aed",
  green: "#059669",
  red: "#dc2626",
  amber: "#d97706",
};

const elements = {
  nameInput: document.querySelector("#nameInput"),
  themeSwatches: document.querySelectorAll(".theme-swatch"),
  getButton: document.querySelector("#getButton"),
  postButton: document.querySelector("#postButton"),
  listButton: document.querySelector("#listButton"),
  requestStatus: document.querySelector("#requestStatus"),
  savedUsers: document.querySelector("#savedUsers"),
  userCard: document.querySelector("#userCard"),
  method: document.querySelector("#method"),
  userName: document.querySelector("#userName"),
  status: document.querySelector("#status"),
  theme: document.querySelector("#theme"),
  userVersion: document.querySelector("#userVersion"),
  message: document.querySelector("#message"),
  detailUserVersion: document.querySelector("#detailUserVersion"),
  pointsPanel: document.querySelector("#pointsPanel"),
  pointsValue: document.querySelector("#pointsValue"),
  rawResponse: document.querySelector("#rawResponse"),
};

function mergeResponse(data) {
  Object.assign(state, data);
  state.points = Number.isInteger(data.points) ? data.points : null;
  state.versions = {
    ...state.versions,
    ...(data.versions || {}),
  };

  if (data.userServiceVersion) {
    state.versions.userService = data.userServiceVersion;
  }
}

function render(data = state) {
  const themeName = data.theme || "blue";
  const themeColor = themeColors[themeName] || themeName;

  document.documentElement.style.setProperty("--theme-color", themeColor);
  elements.themeSwatches.forEach((swatch) => {
    const isSelected = swatch.dataset.theme === themeName;
    swatch.classList.toggle("is-selected", isSelected);
    swatch.setAttribute("aria-checked", String(isSelected));
  });
  elements.method.textContent = data.method || "GetUser";
  elements.userName.textContent = data.name || "demo-user";
  elements.status.textContent = data.status || "unknown";
  elements.theme.textContent = themeName;
  elements.userVersion.textContent = data.userServiceVersion || data.versions.userService || "unknown";
  elements.message.textContent = data.message || "";
  elements.detailUserVersion.textContent = data.versions.userService || "unknown";

  const hasPoints = Number.isInteger(data.points);
  elements.pointsPanel.hidden = !hasPoints;
  elements.pointsValue.textContent = hasPoints ? data.points : "0";
}

function setLoading(isLoading, label = "Ready") {
  elements.getButton.disabled = isLoading;
  elements.postButton.disabled = isLoading;
  elements.listButton.disabled = isLoading;
  elements.requestStatus.textContent = label;
}

function selectedTheme() {
  return (
    document.querySelector(".theme-swatch.is-selected")?.dataset.theme ||
    state.theme ||
    "blue"
  );
}

async function requestUser(method) {
  const name = elements.nameInput.value.trim() || "demo-user";
  const theme = selectedTheme();
  const url = `/api/user?name=${encodeURIComponent(name)}&theme=${encodeURIComponent(theme)}`;

  setLoading(true, `${method} in flight`);
  try {
    const options =
      method === "POST"
        ? {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, theme }),
          }
        : { method };

    const response = await fetch(url, options);
    const data = await response.json();

    elements.rawResponse.textContent = JSON.stringify(data, null, 2);
    if (!response.ok) {
      throw new Error(data.error || `Request failed with ${response.status}`);
    }

    mergeResponse(data);
    render(state);
    setLoading(false, `${method} complete`);
  } catch (error) {
    setLoading(false, error.message);
  }
}

function renderSavedUsers(users) {
  elements.savedUsers.replaceChildren();

  if (!users.length) {
    const empty = document.createElement("p");
    empty.className = "empty-users";
    empty.textContent = "No saved users";
    elements.savedUsers.append(empty);
    return;
  }

  users.forEach((user) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "saved-user-button";
    button.dataset.name = user.name;
    button.dataset.theme = user.theme;
    const name = document.createElement("span");
    name.textContent = user.name;
    const theme = document.createElement("strong");
    theme.textContent = user.theme;
    button.append(name, theme);
    button.addEventListener("click", () => loadSavedUser(user));
    elements.savedUsers.append(button);
  });
}

async function listUsers() {
  setLoading(true, "GET /api/users in flight");
  try {
    const response = await fetch("/api/users");
    const data = await response.json();

    elements.rawResponse.textContent = JSON.stringify(data, null, 2);
    if (!response.ok) {
      throw new Error(data.error || `Request failed with ${response.status}`);
    }

    renderSavedUsers(data.users || []);
    setLoading(false, "GET /api/users complete");
  } catch (error) {
    setLoading(false, error.message);
  }
}

function loadSavedUser(user) {
  elements.nameInput.value = user.name;
  state.theme = user.theme || "blue";
  render(state);
  requestUser("GET");
}

elements.themeSwatches.forEach((swatch) => {
  swatch.addEventListener("click", () => {
    state.theme = swatch.dataset.theme;
    render(state);
  });
});

elements.getButton.addEventListener("click", () => requestUser("GET"));
elements.postButton.addEventListener("click", () => requestUser("POST"));
elements.listButton.addEventListener("click", () => listUsers());

render();
requestUser("GET");

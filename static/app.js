
const $ = (id) => document.getElementById(id);

let timer;

function notice(message) {
  const noticeElement = $("notice");
  if (!noticeElement) return;

  noticeElement.textContent = message;
  noticeElement.classList.add("show");

  clearTimeout(timer);

  timer = setTimeout(() => {
    noticeElement.classList.remove("show");
  }, 5000);
}

async function api(path, method = "GET") {
  const response = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
    },
  });

  const data = await response.json().catch(() => ({
    detail: "Unexpected server response",
  }));

  if (!response.ok) {
    throw new Error(data.detail || "Request failed");
  }

  return data;
}

async function refresh() {
  try {
    const state = await api("/api/state");

    $("credits").textContent = Number(state.credits).toLocaleString();

    const purchases = $("purchases");

    if (state.purchases?.length) {
      purchases.innerHTML = state.purchases
        .map(
          (purchase) => `
            <div class="purchase">
              <span>
                ${purchase.refunded ? "Refunded" : "Credit pack"}
                · ${purchase.credits} credits
              </span>
              <span>
                ${(
                  Number(purchase.amount || 0) / 100
                ).toLocaleString("en-US", {
                  style: "currency",
                  currency: "USD",
                })}
              </span>
            </div>
          `
        )
        .join("");
    } else {
      purchases.textContent =
        "No purchases yet — your first 100 credits are one checkout away.";
    }

    const params = new URLSearchParams(window.location.search);

    if (params.has("checkout")) {
      const checkoutStatus = params.get("checkout");

      if (checkoutStatus === "return") {
        notice(
          "Returned from Checkout. Credits will update after Stripe confirms the payment."
        );
      } else {
        notice("Checkout cancelled. No credits were added.");
      }

      window.history.replaceState({}, "", window.location.pathname);
    }
  } catch (error) {
    notice(error.message || "Could not load account state.");
  }
}

async function redirect(path) {
  try {
    const data = await api(path, "POST");

    if (!data.url) {
      throw new Error("Checkout URL was not returned by the server.");
    }

    window.location.href = data.url;
  } catch (error) {
    notice(error.message || "Could not complete the request.");
  }
}

$("buy").onclick = () => redirect("/api/checkout/credits");

$("subscribe").onclick = () => redirect("/api/checkout/pro");

$("portal").onclick = () => redirect("/api/portal");

$("refresh").onclick = refresh;

$("use").onclick = async () => {
  try {
    const data = await api("/api/use-credit", "POST");

    $("credits").textContent = Number(data.credits).toLocaleString();

    notice("Used 1 credit.");
  } catch (error) {
    notice(error.message || "Could not use a credit.");
  }
};

$("refund").onclick = async () => {
  const confirmed = window.confirm(
    "Request a refund for your latest unrefunded credit pack? Credits are reversed only after Stripe confirms the refund."
  );

  if (!confirmed) return;

  try {
    const data = await api("/api/refund", "POST");

    notice(data.message || `Refund status: ${data.status}`);

    await refresh();
  } catch (error) {
    notice(error.message || "Could not process the refund request.");
  }
};

// Load the latest account balance and purchase history.
refresh();

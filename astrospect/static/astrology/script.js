console.log("script.js loaded");

const geoapifyKey = JSON.parse(
    document.getElementById("geoapify-key").textContent
);

if (!geoapifyKey || typeof autocomplete === "undefined") {
    console.error(
        "Geoapify autocomplete is unavailable. Set GEOAPIFY_API_KEY "
        + "and check that the Geoapify script loaded."
    );
}

let birthplaceAutocomplete;
if (geoapifyKey && typeof autocomplete !== "undefined") {
    birthplaceAutocomplete = new autocomplete.GeocoderAutocomplete(
        document.getElementById("birthplace-autocomplete"),
        geoapifyKey,
        {
            type: "city",
            placeholder: "Enter your birth city or town",
            lang: "en",
            limit: 5
        }
    );
} else {
    document.getElementById("birthplace-manual").hidden = false;
}

function clearBirthplace() {
    console.log("Clearing location");
    document.getElementById("birthplace").value = "";
    document.getElementById("location-id").value = "";
    document.getElementById("latitude").value = "";
    document.getElementById("longitude").value = "";
    document.getElementById("birth-timezone").value = "";
}

birthplaceAutocomplete?.on("select", function (location) {
    console.log("Select event received:", location);
    clearBirthplace();

    if (!location) {
        return;
    }

    const details = location.properties;

    console.log("Selected location time zone:", details.timezone);

    document.getElementById("birthplace").value = details.formatted;
    document.getElementById("location-id").value = details.place_id;
    document.getElementById("latitude").value = details.lat;
    document.getElementById("longitude").value = details.lon;
    document.getElementById("birth-timezone").value = details.timezone?.name ?? "";
    console.log("Location selected:", {
        birthplace: document.getElementById("birthplace").value,
        latitude: document.getElementById("latitude").value,
        longitude: document.getElementById("longitude").value
    });
    
});

// clear the old selection if the user starts changing the location.
document.getElementById("birthplace-autocomplete")
    .addEventListener("input", clearBirthplace);

console.log("Location listeners ready");

const birthChartForm = document.querySelector("form[action]");
const birthplaceStatus = document.getElementById("birthplace-status");
const manualBirthplace = document.getElementById("birthplace-manual");

manualBirthplace.addEventListener("input", clearBirthplace);

function selectedBirthplaceMatches(inputValue) {
    const selectedName = document.getElementById("birthplace").value.trim();
    const locationId = document.getElementById("location-id").value.trim();
    const latitude = document.getElementById("latitude").value;
    const longitude = document.getElementById("longitude").value;
    const timezone = document.getElementById("birth-timezone").value.trim();
    return Boolean(
        selectedName
        && selectedName.toLowerCase() === inputValue.trim().toLowerCase()
        && locationId
        && latitude
        && longitude
        && timezone
    );
}

birthChartForm.addEventListener("submit", () => {
    const locationInput = document.querySelector(
        "#birthplace-autocomplete .geoapify-autocomplete-input"
    ) || manualBirthplace;
    const enteredPlace = locationInput?.value.trim() || "";
    if (!enteredPlace || selectedBirthplaceMatches(enteredPlace)) {
        return;
    }
    clearBirthplace();
    document.getElementById("birthplace").value = enteredPlace;
    birthplaceStatus.textContent = "The entered birthplace will be looked up when you submit.";
});
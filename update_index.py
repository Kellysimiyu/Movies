from pathlib import Path

path = Path("index.html")
html = path.read_text(encoding="utf-8")

html = html.replace(
    '<link rel="preload" href="urls.txt" as="fetch" crossorigin>',
    '<link rel="preload" href="catalog.json" as="fetch" crossorigin>'
)

start_marker = '  /* Load catalogue */'
start = html.find(start_marker)

if start == -1:
    raise SystemExit("ERROR: Could not find catalogue loading section.")

end = html.find('\n\n})();', start)

if end == -1:
    raise SystemExit("ERROR: Could not find end of catalogue loading section.")

new_loader = r'''
  /* Load pre-generated catalogue */

  fetch("catalog.json", { cache: "force-cache" })
    .then(response => {
      if (!response.ok) {
        throw new Error("Could not load catalog.json");
      }
      return response.json();
    })
    .then(catalog => {
      if (!Array.isArray(catalog)) {
        throw new Error("Invalid catalogue format");
      }

      const seen = new Set();

      allVideos = catalog.reduce((videos, item) => {
        if (!item || !item.id || !isRealTitle(item.title)) {
          return videos;
        }

        const id = String(item.id);

        if (!/^[\w-]+$/.test(id) || seen.has(id)) {
          return videos;
        }

        seen.add(id);

        // Category detection happens locally. No YouTube metadata request.
        const title = String(item.title).trim();
        const category = detectCategoryFromTitle(title);

        if (deadVideoCache[id]) {
          return videos;
        }

        videos.push({
          id,
          title,
          category,
          url: item.url || "https://www.youtube.com/watch?v=" + id,
          thumbnail: item.thumbnail ||
            "https://i.ytimg.com/vi/" + id + "/hqdefault.jpg"
        });

        return videos;
      }, []);

      if (!allVideos.length) {
        countEl.textContent = "No videos";
        sectionSub.textContent = "Catalogue is empty";

        grid.innerHTML =
          '<p class="error">No valid videos found in catalog.json.</p>';

        titleIndexInfo.textContent = "No videos found.";
        allVideoTitles.innerHTML = "";
        return;
      }

      // All metadata is already available.
      metadataFinished = true;

      const movieCount = allVideos.filter(
        video => video.category !== "music"
      ).length;

      const musicCount = allVideos.filter(
        video => video.category === "music"
      ).length;

      sectionSub.textContent =
        movieCount + " movie videos · " +
        musicCount + " music videos · catalogue ready";

      // Display all actual titles immediately.
      buildAllVideoTitles();

      // Update structured data using saved titles.
      updateVideoStructuredData();

      // Render immediately.
      resetCatalogue();
      setupInfiniteScroll();

      console.info(
        "Movie Zone catalogue loaded:",
        allVideos.length,
        "videos. No metadata retrieval required."
      );
    })
    .catch(error => {
      console.error("Catalogue loading error:", error);

      countEl.textContent = "Unable to load videos";
      sectionSub.textContent = "Failed to load catalogue";

      grid.innerHTML =
        '<p class="error">Could not load catalog.json. Ensure it is in the same folder as index.html.</p>';

      titleIndexInfo.textContent = "Could not load video titles.";
      allVideoTitles.innerHTML = "";
    });
'''

html = html[:start] + new_loader + html[end:]

path.write_text(html, encoding="utf-8")

print("SUCCESS: Movie Zone now loads catalog.json.")
print("YouTube metadata retrieval has been removed from the loading process.")

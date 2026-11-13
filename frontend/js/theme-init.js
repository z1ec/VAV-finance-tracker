// Applied before the first paint (classic script in <head>) so the stored theme does not flash.
// A separate file instead of an inline <script>: the Content-Security-Policy allows only 'self'.
try {
  const theme = localStorage.getItem("theme");
  if (theme) document.documentElement.setAttribute("data-theme", theme);
} catch (e) {}

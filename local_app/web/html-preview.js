/* Fit fixed HTML canvases without granting scripts or same-origin access. */
(() => {
  "use strict";
  let current = null;
  function close() {
    if (!current) return;
    current.observer?.disconnect();
    if (current.resize) window.removeEventListener("resize", current.resize);
    current = null;
  }
  function mount(content, frame, html) {
    close();
    // Only the sanitizer creates meta elements; source meta tags are discarded.
    const hint = /<meta name="ax-preview-width" content="(\d{3,5})">/.exec(String(html));
    const naturalWidth = hint ? Number(hint[1]) : 0;
    if (!(naturalWidth >= 640 && naturalWidth <= 16416)) {content.append(frame);return;}
    const viewport = document.createElement("div"), toolbar = document.createElement("div"), label = document.createElement("span"), toggle = document.createElement("button");
    viewport.className = "html-preview-viewport";
    toolbar.className = "html-preview-fit";
    label.className = "html-preview-scale";
    toggle.type = "button";toggle.className = "text-button";
    toggle.setAttribute("aria-label", "HTML 원본 크기로 보기");
    toggle.textContent = "원본 크기";
    toolbar.append(label,toggle);viewport.append(frame);content.append(toolbar,viewport);
    const record = {viewport,frame,observer:null,resize:null,fit:true};current=record;
    function resize() {
      if (current !== record || !viewport.isConnected) return;
      const width = viewport.clientWidth, height = viewport.clientHeight;
      if (!width || !height) return;
      const scale = record.fit ? Math.min(1, width / naturalWidth) : 1;
      // Retain a full virtual viewport; its scrollbar still scrolls long reports.
      frame.style.width = (scale < 1 ? naturalWidth : width) + "px";
      frame.style.height = Math.ceil(height / scale) + "px";
      frame.style.transform = scale < 1 ? `scale(${scale})` : "none";
      label.textContent = record.fit ? `너비 맞춤 · ${Math.round(scale * 100)}%` : "원본 크기 · 100%";
      toggle.textContent = record.fit ? "원본 크기" : "너비 맞춤";
      toggle.setAttribute("aria-label", record.fit ? "HTML 원본 크기로 보기" : "HTML 창 너비에 맞추기");
      toggle.setAttribute("aria-pressed", String(!record.fit));
    }
    toggle.onclick = () => {record.fit=!record.fit;resize();};
    if (typeof ResizeObserver === "function") {record.observer=new ResizeObserver(resize);record.observer.observe(viewport);}
    else {record.resize=resize;window.addEventListener("resize",resize);}
    requestAnimationFrame(resize);
  }
  globalThis.WorkspaceHtmlPreview = {mount,close};
})();

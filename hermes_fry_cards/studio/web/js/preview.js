/* CardKit JSON → DOM 轻量渲染器 + markdown 子集（预览专用） */
(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* 取文本：i18n_content 按 Studio 界面语言优先（中 zh_cn / 英 en_us） */
  function pickText(el) {
    if (!el) return "";
    var uiEn = !!(window.FryI18n && window.FryI18n.getLang() === "en");
    if (el.i18n_content) {
      return (uiEn ? el.i18n_content.en_us || el.i18n_content.zh_cn
                   : el.i18n_content.zh_cn || el.i18n_content.en_us) || el.content || "";
    }
    return el.content || "";
  }

  /* 行内 markdown（入参已完成 HTML 转义） */
  function inlineMd(s) {
    var code = [];
    s = s.replace(/`([^`\n]+)`/g, function (_, content) {
      code.push(content);
      return "\u0001" + (code.length - 1) + "\u0002";
    });
    // font 标签经 escapeHtml 后单引号可能仍为 ' 或已变 &#39;，两种形态都要匹配
    s = s.replace(
      /&lt;font color=(?:'|&#39;|&quot;)([a-zA-Z0-9(),. -]+)(?:'|&#39;|&quot;)&gt;([\s\S]*?)&lt;\/font&gt;/g,
      function (m, c, t) { return '<span style="color:' + escapeHtml(colorValue(c)) + '">' + t + "</span>"; },
    );
    s = s.replace(/\*\*\*([^*\n]+)\*\*\*/g, "<b><i>$1</i></b>");
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
    s = s.replace(/\*([^*\n]+)\*/g, "<i>$1</i>");
    s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, function (m, alt) {
      return '<span class="mdimg">🖼 ' + (alt || "image") + "</span>";
    });
    s = s.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
    return s.replace(/\u0001(\d+)\u0002/g, function (_, index) {
      return "<code>" + code[Number(index)] + "</code>";
    });
  }

  function splitCells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (x) { return x.trim(); });
  }

  function buildTable(head, rows) {
    var th = splitCells(head).map(function (c) { return "<th>" + inlineMd(c) + "</th>"; }).join("");
    var tr = rows.map(function (r) {
      return "<tr>" + splitCells(r).map(function (c) { return "<td>" + inlineMd(c) + "</td>"; }).join("") + "</tr>";
    }).join("");
    return "<table><thead><tr>" + th + "</tr></thead><tbody>" + tr + "</tbody></table>";
  }

  function renderMarkdown(raw) {
    var src = escapeHtml(raw);
    var blocks = [];
    var work = src.replace(/```[^\n]*\n([\s\S]*?)```/g, function (m, body) {
      blocks.push(body);
      // 占位符两端补空格防被周边文本吞掉；匹配一律在 trim 后进行
      return "\n CB" + (blocks.length - 1) + " \n";
    });
    var lines = work.split("\n");
    var out = [];
    var i = 0;
    var isTableRow = function (l) { return /^\s*\|.*\|\s*$/.test(l); };
    var isSepRow = function (l) { return l.indexOf("-") >= 0 && /^\s*\|?[\s:|-]+\|[\s:|-]*\s*$/.test(l); };

    while (i < lines.length) {
      var line = lines[i];
      var ph = line.trim().match(/^CB(\d+)$/); // trim 后空格已被剥掉
      if (ph) {
        out.push("<pre><code>" + blocks[parseInt(ph[1], 10)] + "</code></pre>");
        i++;
        continue;
      }
      if (!line.trim()) {
        var blanks = 0;
        while (i < lines.length && !lines[i].trim()) { blanks++; i++; }
        if (out.length && i < lines.length && blanks > 1) {
          out.push('<div class="md-blank-lines" style="height:' + (blanks - 1) + 'em"></div>');
        }
        continue;
      }

      if (isTableRow(line) && i + 1 < lines.length && isSepRow(lines[i + 1])) {
        var head = line;
        var rows = [];
        i += 2;
        while (i < lines.length && isTableRow(lines[i])) { rows.push(lines[i]); i++; }
        out.push(buildTable(head, rows));
        continue;
      }
      var h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        var lvl = h[1].length; // The builder has already applied heading_mode.
        out.push("<h" + lvl + ">" + inlineMd(h[2]) + "</h" + lvl + ">");
        i++;
        continue;
      }
      if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
      if (/^\s*&gt;\s?/.test(line)) {
        var bq = [];
        while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) {
          bq.push(lines[i].replace(/^\s*&gt;\s?/, ""));
          i++;
        }
        out.push("<blockquote>" + inlineMd(bq.join(" ")) + "</blockquote>");
        continue;
      }
      if (/^\s*[-*+]\s+/.test(line)) {
        var ul = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
          ul.push("<li>" + inlineMd(lines[i].replace(/^\s*[-*+]\s+/, "")) + "</li>");
          i++;
        }
        out.push("<ul>" + ul.join("") + "</ul>");
        continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        var ol = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          ol.push("<li>" + inlineMd(lines[i].replace(/^\s*\d+\.\s+/, "")) + "</li>");
          i++;
        }
        out.push("<ol>" + ol.join("") + "</ol>");
        continue;
      }
      var para = [line];
      i++;
      while (
        i < lines.length && lines[i].trim() &&
        !/^(#{1,6}\s|\s*[-*+]\s|\s*\d+\.\s|\s*\|)/.test(lines[i]) &&
        !/^\s*&gt;/.test(lines[i]) &&
        !/^CB\d+$/.test(lines[i].trim())
      ) {
        para.push(lines[i]);
        i++;
      }
      out.push("<p>" + inlineMd(para.join("\n")).replace(/\n/g, "<br>") + "</p>");
    }
    return out.join("\n");
  }

  function darkTheme() {
    return !!(document.documentElement && document.documentElement.getAttribute("data-theme") === "dark");
  }

  function colorValue(value, colors) {
    if (!value || value === "default" || value === "none") return "inherit";
    var custom = colors && colors[value];
    if (custom) return custom[darkTheme() ? "dark_mode" : "light_mode"] || custom.light_mode;
    if (/^rgba\([\d., ]+\)$/.test(value) || /^#[a-fA-F0-9]{6}$/.test(value)) return value;
    if (value === "white" || value === "bg-white") return darkTheme() ? "#212224" : "#ffffff";
    var hues = {blue: 222, carmine: 328, green: 116, indigo: 237, lime: 83, grey: 215,
      orange: 31, purple: 262, red: 2, sunflower: 47, turquoise: 173, violet: 301, wathet: 197, yellow: 45};
    var match = String(value).match(/^([a-z]+)(?:-(\d+))?$/);
    if (!match || !Object.prototype.hasOwnProperty.call(hues, match[1])) return "inherit";
    var level = match[2] === undefined ? 500 : Number(match[2]);
    var lightness = Math.max(5, Math.min(98, 100 - level / 10));
    if (darkTheme()) lightness = 100 - lightness;
    return "hsl(" + hues[match[1]] + ", " + (match[1] === "grey" ? 5 : 65) + "%, " + lightness + "%)";
  }

  function spacingValue(value) {
    return ({small: "4px", medium: "8px", large: "16px", extra_large: "24px"})[value] || value;
  }

  function applyStyle(node, el, colors, textSizes) {
    if (!el) return;
    var style = node.style || (node.style = {});
    ["padding", "margin", "width"].forEach(function (key) {
      if (el[key] !== undefined && el[key] !== "fill" && el[key] !== "auto_when_fold") style[key] = String(el[key]);
    });
    var sizes = {"heading-0": 30, "heading-1": 24, "heading-2": 20, "heading-3": 18, "heading-4": 16,
      heading: 16, normal: 14, normal_v2: 14, notation: 12, "xxxx-large": 30, "xxx-large": 24,
      "xx-large": 20, "x-large": 18, large: 16, medium: 14, small: 12, "x-small": 10};
    var custom = textSizes && Object.prototype.hasOwnProperty.call(textSizes, el.text_size) && textSizes[el.text_size];
    var size = custom ? custom.pc || custom.default || "normal" : el.text_size;
    if (sizes[size]) style.fontSize = sizes[size] + "px";
    if (el.text_align) style.textAlign = el.text_align;
    if (el.text_color) style.color = colorValue(el.text_color, colors);
    if (el.bold !== undefined) style.fontWeight = el.bold ? "700" : "400";
    if (el.italic !== undefined) style.fontStyle = el.italic ? "italic" : "normal";
    if (el.background_color || el.background_style) style.backgroundColor = colorValue(el.background_color || el.background_style, colors);
    if (el.horizontal_spacing !== undefined) style.columnGap = spacingValue(el.horizontal_spacing);
    if (el.vertical_spacing !== undefined) style.rowGap = spacingValue(el.vertical_spacing);
    if (el.horizontal_align) style.alignItems = ({left: "flex-start", center: "center", right: "flex-end"})[el.horizontal_align];
    if (el.vertical_align) style.justifyContent = ({top: "flex-start", center: "center", bottom: "flex-end"})[el.vertical_align];
  }

  function appendIcon(parent, icon, colors) {
    if (!icon) return null;
    var chip = document.createElement("span");
    chip.className = "icon-chip";
    chip.title = icon.token || icon.img_key || "";
    chip.textContent = icon.token || "▧";
    if (!chip.style) chip.style = {};
    if (icon.color) chip.style.color = colorValue(icon.color, colors);
    if (icon.size) {
      var size = String(icon.size).split(/\s+/);
      chip.style.width = size[0];
      chip.style.height = size[1] || size[0];
    }
    parent.appendChild(chip);
    return chip;
  }

  function nativeTable(el, colors) {
    var wrap = document.createElement("div");
    wrap.className = "native-table-wrap";
    applyStyle(wrap, {margin: el.margin}, colors);
    var table = document.createElement("table");
    table.className = "native-table" + (el.freeze_first_column ? " freeze-first" : "");
    var head = document.createElement("thead");
    var hr = document.createElement("tr");
    var columns = el.columns || [];
    columns.forEach(function (column) {
      var th = document.createElement("th");
      var heading = document.createElement("div");
      heading.innerHTML = renderMarkdown(column.display_name || column.name);
      th.appendChild(heading);
      applyStyle(th, el.header_style || {}, colors);
      th.style.width = column.width || "auto";
      if (el.header_style && el.header_style.lines) {
        heading.style.maxHeight = el.header_style.lines * 1.6 + "em";
        heading.style.overflow = "hidden";
      }
      hr.appendChild(th);
    });
    head.appendChild(hr);
    table.appendChild(head);
    var body = document.createElement("tbody");
    var pageSize = el.page_size || (el.rows || []).length;
    (el.rows || []).slice(0, pageSize).forEach(function (row) {
      var tr = document.createElement("tr");
      columns.forEach(function (column) {
        var td = document.createElement("td");
        var cell = document.createElement("div");
        cell.innerHTML = renderMarkdown(row[column.name] == null ? "" : row[column.name]);
        cell.style.maxHeight = el.row_max_height || "none";
        cell.style.overflow = "auto";
        td.style.height = ({low: "32px", middle: "54px", high: "72px"})[el.row_height] || el.row_height || "auto";
        td.style.width = column.width || "auto";
        td.style.textAlign = column.horizontal_align || el.horizontal_align || "left";
        td.style.verticalAlign = ({center: "middle"})[column.vertical_align || el.vertical_align] || column.vertical_align || el.vertical_align || "top";
        td.appendChild(cell);
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    wrap.appendChild(table);
    if ((el.rows || []).length > pageSize) {
      var note = document.createElement("small");
      note.textContent = "1–" + pageSize + " / " + el.rows.length;
      wrap.appendChild(note);
    }
    return wrap;
  }

  function renderEl(el, mount, colors, textSizes) {
    if (!el || typeof el !== "object") return;
    switch (el.tag) {
      case "column_set": {
        var surface = document.createElement("div");
        surface.className = "card-surface";
        applyStyle(surface, el, colors, textSizes);
        if (el.background_style) surface.style.borderRadius = "8px";
        (el.columns || []).forEach(function (column) {
          applyStyle(surface, column, colors, textSizes);
          (column.elements || []).forEach(function (child) { renderEl(child, surface, colors, textSizes); });
        });
        mount.appendChild(surface);
        break;
      }
      case "markdown": {
        var div = document.createElement("div");
        div.className = "md" + (el.text_size ? " ts-" + el.text_size : "");
        applyStyle(div, el, colors, textSizes);
        var mdIcon = appendIcon(div, el.icon, colors);
        if (mdIcon && (el.element_id === "loading_icon" || el.element_id === "loading_cursor")) mdIcon.className += " loading-icon";
        var content = document.createElement("div");
        content.innerHTML = renderMarkdown(pickText(el));
        div.appendChild(content);
        mount.appendChild(div);
        break;
      }
      case "collapsible_panel": {
        var d = document.createElement("details");
        d.className = "cp" + (el.border && el.border.color ? " border-" + el.border.color : "");
        applyStyle(d, el, colors, textSizes);
        if (!el.border) d.style.border = "none";
        else {
          d.style.borderColor = colorValue(el.border.color, colors);
          if (el.border.corner_radius) d.style.borderRadius = el.border.corner_radius;
        }
        if (el.expanded) d.open = true;
        var sum = document.createElement("summary");
        var header = el.header || {};
        if (header.position === "bottom") d.className += " cp-bottom";
        applyStyle(sum, header, colors, textSizes);
        sum.style.display = "flex";
        sum.style.alignItems = ({top: "flex-start", bottom: "flex-end"})[header.vertical_align] || "center";
        sum.style.gap = "8px";
        var icon = appendIcon(sum, header.icon, colors);
        if (icon) {
          icon.style.order = header.icon_position === "right" ? "2" : "0";
          var angle = header.icon_expanded_angle || 0;
          function rotateIcon() { icon.style.transform = "rotate(" + (d.open ? angle : 0) + "deg)"; }
          rotateIcon();
          d.addEventListener("toggle", rotateIcon);
        }
        var title = document.createElement("span");
        applyStyle(title, header.title, colors, textSizes);
        if (header.title && (header.title.tag === "markdown" || header.title.tag === "lark_md")) title.innerHTML = renderMarkdown(pickText(header.title));
        else title.textContent = pickText(header.title);
        sum.appendChild(title);
        d.appendChild(sum);
        var kids = document.createElement("div");
        kids.className = "cp-children";
        applyStyle(kids, {vertical_spacing: el.vertical_spacing, horizontal_spacing: el.horizontal_spacing,
          horizontal_align: el.horizontal_align, vertical_align: el.vertical_align}, colors, textSizes);
        (el.elements || []).forEach(function (c) { renderEl(c, kids, colors, textSizes); });
        d.appendChild(kids);
        mount.appendChild(d);
        break;
      }
      case "div": {
        var row = document.createElement("div");
        row.className = "tool-row";
        applyStyle(row, el, colors, textSizes);
        appendIcon(row, el.icon, colors);
        if (el.text) {
          var span = document.createElement("div");
          var ts = el.text.text_size ? " ts-" + el.text.text_size : "";
          if (el.text.tag === "lark_md") {
            span.className = "md" + ts;
            span.innerHTML = renderMarkdown(pickText(el.text));
          } else {
            span.className = ts.trim();
            span.textContent = pickText(el.text);
          }
          applyStyle(span, el.text, colors, textSizes);
          row.appendChild(span);
        }
        mount.appendChild(row);
        break;
      }
      case "table":
        mount.appendChild(nativeTable(el, colors));
        break;
      case "action":
      case "form": {
        var group = document.createElement("div");
        group.className = "preview-actions";
        (el.actions || el.elements || []).forEach(function (child) { renderEl(child, group, colors, textSizes); });
        mount.appendChild(group);
        break;
      }
      case "button": {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "btn " + (el.type || "default");
        button.textContent = pickText(el.text);
        button.disabled = true;
        mount.appendChild(button);
        break;
      }
      case "input": {
        var input = document.createElement("textarea");
        input.placeholder = pickText(el.placeholder);
        input.disabled = true;
        mount.appendChild(input);
        break;
      }
      case "hr":
        mount.appendChild(document.createElement("hr"));
        break;
      default: {
        var tag = document.createElement("span");
        tag.className = "unknown-el";
        tag.textContent = (window.FryI18n ? window.FryI18n.t("pv.element") : "元素 ") + el.tag;
        mount.appendChild(tag);
      }
    }
  }

  function renderCard(card) {
    var root = document.createElement("div");
    root.className = "feishu-card width-" + ((card.config && card.config.width_mode) || "default");
    var colors = card.config && card.config.style && card.config.style.color;
    var textSizes = card.config && card.config.style && card.config.style.text_size;
    if (card.header) {
      var h = document.createElement("div");
      h.className = "card-header t-" + (card.header.template || "default");
      h.textContent = pickText(card.header.title);
      applyStyle(h, {padding: card.header.padding}, colors, textSizes);
      appendIcon(h, card.header.icon, colors);
      root.appendChild(h);
    }
    var body = document.createElement("div");
    body.className = "card-body";
    applyStyle(body, card.body, colors, textSizes);
    var els = (card.body && card.body.elements) || card.elements || [];
    if (!els.length) {
      var empty = document.createElement("div");
      empty.className = "preview-empty";
      empty.textContent = window.FryI18n ? window.FryI18n.t("pv.empty") : "(无元素)";
      body.appendChild(empty);
    }
    els.forEach(function (el) { renderEl(el, body, colors, textSizes); });
    root.appendChild(body);
    return root;
  }

  window.FryPreview = { renderCard: renderCard, renderMarkdown: renderMarkdown, escapeHtml: escapeHtml };
})();

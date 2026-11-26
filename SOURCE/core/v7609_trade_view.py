"""Read-only paired trade tables. Selection reuses the existing chart event path."""
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont
from datetime import date, datetime, timedelta, timezone

PAGE_SIZE = 10
VISIBLE_ROWS = 10
HOLD = '매수종목'
SOLD = '매도종목'
SOLD_STYLE = 'V7609Sold.V7609Trade.Treeview'   # 글꼴은 V7609Trade를 물려받고 줄 높이만 따로
MIN_REASON = 150      # V7619: 매도사유 칸 최소 폭(px)
FIT_PAD = 14          # 글자 폭 + 좌우 여백
HEADINGS = {'no': ('순번', 44), 'name': ('종목명', 98),
    'buy_date': ('매수일', 76), 'buy_time': ('매수시각', 66), 'buy_price': ('매수가', 67),
    'current_price': ('현재가', 67), 'sell_date': ('매도일', 76),
    'sell_time': ('매도시각', 66), 'sell_price': ('매도가', 67),
    'net_pnl': ('순손익(원)', 90), 'net_pnl_pct': ('순손익률', 72), 'sell_reason': ('매도사유', 152),
    'quantity': ('수량', 56), 'invested': ('매수금액', 92)}
HOLD_LABELS = {'buy_price': '평단가', 'buy_time': '첫 매수'}   # V7633: 보유표는 평단·첫 매수시각을 보여준다
TRANCHE_TAG = 'tranche'   # V7633: 2·3차까지 산 종목 아래에 차수별 수량·금액 줄


SECTIONS = ('오늘 매수', '전일 매수', '전전일~ 매수')   # V7630: 보유종목을 매수일별 칸으로 나눈다


def previous_trading_day(today):
    """직전 평일(공휴일은 구분하지 않는다)."""
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def hold_section(buy_date, today):
    """0=오늘 매수, 1=전일 매수, 2=전전일 이전 매수. 날짜를 모르면 오늘로 둔다."""
    try:
        bought = date.fromisoformat(str(buy_date)[:10])
    except ValueError:
        return 0
    if bought >= today:
        return 0
    return 1 if bought >= previous_trading_day(today) else 2


def sectioned(rows, today):
    """[(구역번호, 구역 안 종목들)] — 빈 구역도 항상 포함(칸이 늘 보이게)."""
    groups = [[], [], []]
    for row in rows:
        groups[hold_section(row.get('buy_date'), today)].append(row)
    return list(enumerate(groups))


def page_rows(rows, page):
    """Retained for existing callers/checks; the tables themselves now scroll."""
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    return rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], page, pages


def row_key(stage, row, index):
    """Same-day repeat sells of one symbol must stay separate table rows."""
    parts = [str(stage), str(row.get('symbol', '') or index)]
    if stage == SOLD:
        parts += [str(row.get('sell_date', '') or ''), str(row.get('sell_time', '') or ''),
                  str(row.get('sell_cycle', '') or '')]
    return '|'.join(parts).replace(' ', '_')


def display_value(row, key):
    value = row.get(key)
    if value is None or value == '':
        return '-'
    if key == 'net_pnl_pct':
        return f'{float(value):+.2f}%'
    if key == 'net_pnl':
        return f'{float(value):+,.0f}'
    if key in ('quantity', 'invested'):
        return f'{float(value):,.0f}'
    if key.endswith('price'):
        return f'{float(value):,.0f}'
    return str(value)


def total_invested(rows):
    """V7633: 보유 종목 매수금액 합계(차수 합)."""
    total = 0.0
    for row in rows:
        try:
            total += float(row.get('invested') or 0)
        except (TypeError, ValueError):
            pass
    return total


def tranche_rows(row):
    """V7633: 분할매수 체결 목록 [{tranche, time, price, quantity, notional}] (없으면 빈 목록)."""
    out = []
    for fill in row.get('tranche_fills') or []:
        try:
            out.append(dict(tranche=int(fill.get('tranche', len(out) + 1)), time=str(fill.get('time', '') or ''),
                            price=float(fill.get('price', 0) or 0), quantity=int(fill.get('quantity', 0) or 0),
                            notional=float(fill.get('notional', 0) or 0)))
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def tranche_value(fill, key, row):
    """차수 줄에 들어갈 글자. 날짜는 첫 매수와 다를 때만 쓴다."""
    stamp = fill['time']
    day, clock = (stamp[:10], stamp[11:19]) if len(stamp) >= 19 else ('', stamp)
    if key == 'name':
        return f"   └ {fill['tranche']}차"
    if key == 'buy_date':
        return day if day and day != str(row.get('buy_date', '')) else ''
    if key == 'buy_time':
        return clock or '-'
    if key == 'buy_price':
        return f"{fill['price']:,.0f}"
    if key == 'quantity':
        return f"{fill['quantity']:,}"
    if key == 'invested':
        return f"{(fill['notional'] or fill['price'] * fill['quantity']):,.0f}"
    return ''


def short_reason(text):
    """화면용 매도사유 줄임(버전 꼬리표만 뺌). 원문은 행을 누르면 아래에 전체 표시."""
    text = str(text)
    for tag in ('V7613 ', 'V7609 ', 'V76 '):
        text = text.replace(tag, '')
    return ' '.join(text.split())


def wrap_text(text, measure, room, max_lines):
    """글자 폭 기준 줄바꿈. 마지막 줄이 넘치면 '…'."""
    lines, current = [], ''
    for char in str(text):
        if current and measure(current + char) > room:
            lines.append(current)
            current = char.lstrip()
        else:
            current += char
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        last = lines[max_lines - 1]
        while last and measure(last + '…') > room:
            last = last[:-1]
        lines = lines[:max_lines - 1] + [last + '…']
    return lines or ['-']


def unique_names(rows):
    codes = {}
    for row in rows:
        if row.get('name') and row.get('symbol'):
            codes.setdefault(row['name'], set()).add(row['symbol'])
    return ({name: next(iter(values)) for name, values in codes.items() if len(values) == 1},
            {name for name, values in codes.items() if len(values) > 1})


class TradeView:
    def __init__(self, root, gui, palettes):
        self.root, self.gui = root, gui
        self.original = gui.tree
        self.host = self.original.master
        self.frame = ttk.Frame(self.host)
        self.tables, self.labels, self.row_maps = {}, {}, {}
        self.last_data = None
        self.shown = False
        self.forwarding = False
        self.layout_mode = 'side'
        self.panels, self.hbars, self.needed = {}, {}, {}
        self.original_widgets = [(w, w.pack_info()) for w in self.host.pack_slaves()]
        ttk.Style(root).configure('V7609Trade.Treeview', rowheight=31, font=('맑은 고딕', 9))
        ttk.Style(root).map('V7609Trade.Treeview', foreground=[])
        for index, stage in enumerate((HOLD, SOLD)):
            panel = ttk.Frame(self.frame)
            panel.grid(row=0, column=index, sticky='nsew', padx=(0, 5) if index == 0 else (5, 0))
            self.frame.columnconfigure(index, weight=1, uniform='trade', minsize=0)
            bg, fg = palettes[stage]
            title = tk.Label(panel, text='보유종목' if stage == HOLD else '오늘 매도',
                bg=bg, fg=fg, font=('맑은 고딕', 11, 'bold'), pady=3)   # V7633: 7→3
            title.grid(row=0, column=0, columnspan=2, sticky='ew')
            columns = ('no', 'name', 'buy_date', 'buy_time', 'buy_price')
            columns += ('quantity', 'invested', 'current_price') if stage == HOLD else ('sell_date', 'sell_time', 'sell_price')
            columns += ('net_pnl', 'net_pnl_pct')
            if stage == SOLD:
                columns += ('sell_reason',)
            table = ttk.Treeview(panel, columns=columns, show='headings', height=VISIBLE_ROWS,
                selectmode='browse', style=SOLD_STYLE if stage == SOLD else 'V7609Trade.Treeview')
            for key in columns:
                label, width = HEADINGS[key]
                if stage == HOLD:
                    label = HOLD_LABELS.get(key, label)
                table.heading(key, text=label)
                table.column(key, width=width, minwidth=width, stretch=key in ('name', 'sell_reason'),
                    anchor='w' if key in ('name', 'sell_reason') else 'center')
            table.tag_configure('gain', background=bg, foreground=palettes['gain'])
            table.tag_configure('loss', background=bg, foreground=palettes['loss'])
            table.tag_configure('flat', background=bg, foreground=fg)
            table.tag_configure('section', background='#1f4f7a', foreground='#ffffff', font=('맑은 고딕', 10, 'bold'))
            table.tag_configure(TRANCHE_TAG, background='#10283f', foreground='#b9d3ea')
            # Every row stays reachable: vertical scroll for count, horizontal for width.
            vertical = ttk.Scrollbar(panel, orient='vertical', command=table.yview)
            horizontal = ttk.Scrollbar(panel, orient='horizontal', command=table.xview)
            table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            table.grid(row=1, column=0, sticky='nsew')
            vertical.grid(row=1, column=1, sticky='ns')
            horizontal.grid(row=2, column=0, sticky='ew')
            panel.rowconfigure(1, weight=1)
            panel.columnconfigure(0, weight=1)
            label = ttk.Label(panel, anchor='center')
            label.grid(row=3, column=0, columnspan=2, sticky='ew', pady=4)
            self.tables[stage], self.labels[stage], self.row_maps[stage] = table, label, {}
            self.panels[stage], self.hbars[stage] = panel, horizontal
            if stage == SOLD:
                # '오늘 매도'의 매도일은 항상 오늘 → 칸을 숨겨 매수일·매도사유가 다 보이게 한다(자료는 그대로).
                table.configure(displaycolumns=tuple(k for k in columns if k != 'sell_date'))
            table.bind('<<TreeviewSelect>>', lambda e, s=stage: self.select(s))
            table.bind('<Return>', lambda e, s=stage: (self.select(s), 'break')[1])
            table.bind('<MouseWheel>', self.wheel)
            table.bind('<Prior>', lambda e, t=table: (t.yview_scroll(-1, 'pages'), 'break')[1])
            table.bind('<Next>', lambda e, t=table: (t.yview_scroll(1, 'pages'), 'break')[1])
            # Prevent the old root navigation handler from moving the hidden table.
            for key, delta in (('<Up>', -1), ('<Down>', 1), ('<Left>', -1), ('<Right>', 1)):
                table.bind(key, lambda e, s=stage, d=delta: self.arrow(s, d))
        self.frame.rowconfigure(0, weight=1)
        self.frame.bind('<Configure>', lambda e: self.relayout(), add='+')
        self.merge_controls()
        self.poll()

    def merge_controls(self):
        pending = list(self.root.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, ttk.Radiobutton):
                if str(widget.cget('value')) == SOLD:
                    widget.pack_forget()
                elif str(widget.cget('value')) == HOLD:
                    widget.configure(textvariable='', text='오늘 매수·매도')
            if isinstance(widget, tk.Label):
                text = str(widget.cget('text'))
                if text.startswith('04') and '보유' in text:
                    widget.configure(text='04  오늘 매수·매도')
                    parent = widget.master
                    if parent.winfo_manager() == 'pack' and parent.master.winfo_manager() == 'grid':
                        card = parent.master
                        for child in card.winfo_children():
                            if isinstance(child, tk.Label) and child.cget('textvariable'):
                                self.combined_count = child
                elif text.startswith('05') and '오늘매도' in text:
                    parent = widget.master
                    if parent.winfo_manager() == 'pack' and parent.master.winfo_manager() == 'grid':
                        card = parent.master
                        column = int(card.grid_info()['column'])
                        card.master.columnconfigure(column, weight=0, minsize=0)
                        card.grid_remove()
                    elif parent.winfo_manager() == 'pack':
                        parent.pack_forget()

    def wheel(self, event):
        event.widget.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def arrow(self, stage, delta):
        tree = self.tables[stage]
        items = tree.get_children()
        if items:
            selected = tree.selection()
            current = items.index(selected[0]) if selected and selected[0] in items else 0
            position = max(0, min(len(items)-1, current + delta))
            while str(items[position]).startswith('section|') and 0 <= position + delta < len(items):
                position += delta     # V7630: 구역 제목 줄은 건너뛴다
            if str(items[position]).startswith('section|'):
                return 'break'
            iid = items[position]
            tree.selection_set(iid)
            tree.focus(iid)
            tree.see(iid)
        return 'break'

    def select(self, stage):
        if self.forwarding:
            return
        table = self.tables[stage]
        selection = table.selection()
        row = self.row_maps[stage].get(selection[0]) if selection else None
        if not row:
            return
        if stage == SOLD and row.get('sell_reason'):
            self.labels[stage].configure(text=f"매도사유: {row.get('sell_reason')}")
        self.forwarding = True
        try:
            self.gui.choose_category(stage)
            # Name is a display key already resolved by the unchanged dashboard.
            for iid in self.original.get_children():
                values = self.original.item(iid, 'values')
                if len(values) > 2 and values[2] == row['name']:
                    self.original.selection_set(iid)
                    self.original.focus(iid)
                    self.original._v7609_navigate_selected(None)
                    break
            table.focus_set()
        finally:
            self.forwarding = False

    def render(self):
        rows = list(getattr(self.gui, 'last_gui_rows', []))
        if rows == self.last_data:
            return
        self.last_data = [dict(row) for row in rows]
        for stage, table in self.tables.items():
            matching = [row for row in rows if row.get('stage') == stage]
            self.row_maps[stage] = {}
            retained, order = set(), []
            if stage == HOLD:
                today = datetime.now(timezone(timedelta(hours=9))).date()
                entries = []
                for number, group in sectioned(matching, today):
                    entries.append((None, number, len(group)))
                    for row in group:
                        entries.append((row, number, 0))
                        fills = tranche_rows(row)
                        if len(fills) >= 2:   # V7633: 2·3차까지 샀으면 차수별 줄을 바로 아래에
                            entries += [(row, number, fill) for fill in fills]
            else:
                entries = [(row, 0, 0) for row in matching]
            index = 0
            parent_iid = ''
            for row, number, size in entries:
                if row is None:     # 구역 제목 줄(선택해도 아무 동작 없음)
                    iid = f'section|{number}'
                    values = [f'■ {SECTIONS[number]}' if key == 'name' else (f'{size}종목' if key == 'buy_date' else '')
                              for key in table['columns']]
                    tag = 'section'
                elif isinstance(size, dict):   # 차수 줄: 고르면 그 종목을 고른 것과 같다
                    iid = f"{parent_iid}|t{size['tranche']}"
                    values = [tranche_value(size, key, row) for key in table['columns']]
                    tag = TRANCHE_TAG
                else:
                    index += 1
                    values = [str(index) if key == 'no' else display_value(row, key)
                              for key in table['columns']]
                    pnl = row.get('net_pnl')
                    tag = 'flat' if pnl is None or pnl == 0 else ('gain' if pnl > 0 else 'loss')
                    iid = row_key(stage, row, index)
                    while iid in retained:
                        iid = f'{iid}#{index}'
                    parent_iid = iid
                retained.add(iid)
                order.append(iid)
                if table.exists(iid):
                    table.item(iid, values=values, tags=(tag,))
                else:
                    table.insert('', 'end', iid=iid, values=values, tags=(tag,))
                if row is not None:
                    self.row_maps[stage][iid] = row
            for iid in table.get_children():
                if iid not in retained:
                    table.delete(iid)
            for position, iid in enumerate(order):
                table.move(iid, '', position)
            self.labels[stage].configure(
                text=f'총 {len(matching)}건 · 세로 스크롤로 전체 확인 · 행을 누르면 매도사유 전체 표시'
                if stage == SOLD else f'총 {len(matching)}건 · 매수금액 합계 {total_invested(matching):,.0f}원 · 세로 스크롤로 전체 확인')
        self.fit()
        if hasattr(self, 'combined_count'):
            counts = {s: sum(r.get('stage') == s for r in rows) for s in (HOLD, SOLD)}
            self.combined_count.configure(textvariable='', text=f'보유 {counts[HOLD]} · 매도 {counts[SOLD]}')

    # ---------------------------------------------------------------- V7619 칸 맞춤
    def _fonts(self):
        style = ttk.Style(self.root)
        body = style.lookup('V7609Trade.Treeview', 'font') or 'TkDefaultFont'
        head = style.lookup('Treeview.Heading', 'font') or 'TkHeadingFont'
        return tkfont.Font(root=self.root, font=body), tkfont.Font(root=self.root, font=head)

    def fit(self):
        """글자 실제 폭으로 칸을 맞춘다(날짜 2026-11-10 끝까지). 매도사유는 남는 폭을 쓰고 넘치면 '…'."""
        try:
            body, head = self._fonts()
        except tk.TclError:
            return
        for stage, table in self.tables.items():
            total = 0
            shown = [k for k in table['displaycolumns'] if k != '#all'] or list(table['columns'])
            for key in shown:
                if key == 'sell_reason':
                    continue
                texts = [table.set(iid, key) for iid in table.get_children() if not str(iid).startswith('section|')]
                sample = {'buy_date': '2026-11-10', 'sell_date': '2026-11-10',
                          'buy_time': '00:00:00', 'sell_time': '00:00:00'}.get(key, '')
                width = max([head.measure(HEADINGS[key][0]), body.measure(sample)]
                            + [body.measure(t) for t in texts]) + FIT_PAD
                table.column(key, width=width, minwidth=width, stretch=(stage == HOLD))   # 보유표: 남는 폭을 칸마다 고르게
                total += width
            if 'sell_reason' in shown:
                table.column('sell_reason', minwidth=MIN_REASON, stretch=True)
                total += MIN_REASON
            self.needed[stage] = total + 22   # 세로 스크롤바
        self.relayout()
        self.root.after_idle(self.elide_reasons)

    def elide_reasons(self):
        table = self.tables.get(SOLD)
        if table is None or not table.winfo_exists():
            return
        try:
            body, _head = self._fonts()
            room = int(table.column('sell_reason', 'width')) - 12
        except tk.TclError:
            return
        texts, two_lines = {}, False
        for iid, row in self.row_maps.get(SOLD, {}).items():
            lines = wrap_text(short_reason(display_value(row, 'sell_reason')), body.measure, room, 2)
            two_lines = two_lines or len(lines) > 1
            texts[iid] = '\n'.join(lines)
        try:
            base = int(ttk.Style(self.root).lookup('V7609Trade.Treeview', 'rowheight') or 34)
            ttk.Style(self.root).configure(SOLD_STYLE, rowheight=(body.metrics('linespace') * 2 + 10)
                                           if two_lines else base)
        except (tk.TclError, ValueError):
            pass
        for iid, text in texts.items():
            if table.exists(iid) and table.set(iid, 'sell_reason') != text:
                table.set(iid, 'sell_reason', text)

    def relayout(self):
        """두 표가 나란히 다 보이면 나란히, 모자라면 위(보유)·아래(매도)로 쌓는다. 가로 스크롤은 필요할 때만."""
        try:
            width = int(self.frame.winfo_width())
        except tk.TclError:
            return
        if width <= 1 or not self.needed:
            return
        need_side = 2 * max(self.needed.values()) + 10   # 나란히 보기는 반반으로 나눔
        mode = self.layout_mode
        if mode == 'side' and width < need_side:
            mode = 'stack'
        elif mode == 'stack' and width >= need_side + 20:
            mode = 'side'
        if mode != self.layout_mode:
            self.layout_mode = mode
            for index, stage in enumerate((HOLD, SOLD)):
                panel = self.panels[stage]
                if mode == 'side':
                    panel.grid(row=0, column=index, sticky='nsew', padx=(0, 5) if index == 0 else (5, 0), pady=0)
                else:
                    panel.grid(row=index, column=0, sticky='nsew', padx=0, pady=(0, 4) if index == 0 else (4, 0))
            if mode == 'side':
                self.frame.grid_propagate(True)
                self.frame.columnconfigure(0, weight=1, uniform='trade', minsize=0)
                self.frame.columnconfigure(1, weight=1, uniform='trade', minsize=0)
                self.frame.rowconfigure(0, weight=1)
                self.frame.rowconfigure(1, weight=0)
            else:
                self.frame.grid_propagate(False)
                self.frame.columnconfigure(0, weight=1, uniform='', minsize=0)
                self.frame.columnconfigure(1, weight=0, uniform='', minsize=0)
                self.frame.rowconfigure(0, weight=3, uniform='stack')   # V7630: 보유가 이월되어 쌓이므로 보유 칸을 더 크게
                self.frame.rowconfigure(1, weight=2, uniform='stack')
        room = width if mode == 'stack' else (width - 10) // 2
        for stage, bar in self.hbars.items():
            try:
                if self.needed.get(stage, 0) > room:
                    if not bar.winfo_manager():
                        bar.grid()
                elif bar.winfo_manager():
                    bar.grid_remove()
            except tk.TclError:
                pass
        self.root.after_idle(self.elide_reasons)

    def poll(self):
        if not self.root.winfo_exists():
            return
        enabled = self.gui.category_filter_var.get() in (HOLD, SOLD)
        if enabled != self.shown:
            self.shown = enabled
            if enabled:
                for widget, _ in self.original_widgets:
                    widget.pack_forget()
                self.frame.pack(fill='both', expand=True)
            else:
                self.frame.pack_forget()
                for widget, options in self.original_widgets:
                    widget.pack(**options)
        self.render()
        self.root.after(300, self.poll)


def install_command_submit(root, gui, compiler, hub):
    """Enter explicitly submits simple commands; typing alone never places orders."""
    pending = list(root.winfo_children())
    while pending:
        widget = pending.pop()
        pending.extend(widget.winfo_children())
        if not isinstance(widget, tk.Text):
            continue
        try:
            if str(widget.master.cget('text')) != '형 의견·명령 입력':
                continue
        except tk.TclError:
            continue
        status = ttk.Label(widget.master, text='종목명 매수해 / 매도해 / 매수 중지 → Enter 실행 · Shift+Enter 줄바꿈')
        # The existing command panel is grid-managed.
        status.grid(row=99, column=0, columnspan=4, sticky='w')
        def submit(event, input_widget=widget, label=status):
            if event.state & 1:
                return None
            text = input_widget.get('1.0', 'end-1c').strip()
            known, ambiguous = unique_names(getattr(gui, 'last_gui_rows', []))
            selected = gui.tree.selection()
            values = gui.tree.item(selected[0], 'values') if selected else ()
            name = str(values[2]) if len(values) > 2 else ''
            if any(value in text for value in ambiguous) or (not text[:6].isdigit() and name in ambiguous):
                label.configure(text='동일 이름의 종목이 여럿입니다. 6자리 종목코드로 지정하세요.')
                return 'break'
            draft = compiler(text, selected_symbol=known.get(name, ''), selected_name=name, known_names=known)
            if draft.errors or draft.action not in ('IMMEDIATE_BUY', 'IMMEDIATE_SELL', 'PAUSE_BUY', 'BLOCK_BUY'):
                label.configure(text=' · '.join(draft.errors) or '즉시명령 아님: 기존 뜻 확인·적용 버튼을 사용하세요.')
                return 'break'
            # The existing buttons refuse a symbol-less order; Enter must refuse it too.
            if draft.action in ('IMMEDIATE_BUY', 'IMMEDIATE_SELL') and not str(draft.symbol or '').isdigit():
                label.configure(text='종목을 먼저 선택하거나 종목명을 함께 입력하세요.')
                return 'break'
            hub.submit({'kind': draft.action, 'symbol': draft.symbol, 'name': draft.name, 'note': text})
            input_widget.delete('1.0', 'end')
            label.configure(text='PAPER 명령 접수 · 실제 처리 결과는 기존 상태란에 표시됩니다.')
            return 'break'
        widget.bind('<Return>', submit)

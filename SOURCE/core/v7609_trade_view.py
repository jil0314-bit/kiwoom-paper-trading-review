"""Read-only paired trade tables. Selection reuses the existing chart event path."""
import tkinter as tk
from tkinter import ttk

PAGE_SIZE = 10
VISIBLE_ROWS = 10
HOLD = '매수종목'
SOLD = '매도종목'
HEADINGS = {'no': ('순번', 44), 'name': ('종목명', 98),
    'buy_date': ('매수일', 76), 'buy_time': ('매수시각', 66), 'buy_price': ('매수가', 67),
    'current_price': ('현재가', 67), 'sell_date': ('매도일', 76),
    'sell_time': ('매도시각', 66), 'sell_price': ('매도가', 67),
    'net_pnl': ('순손익(원)', 90), 'net_pnl_pct': ('순손익률', 72), 'sell_reason': ('매도사유', 152)}


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
    if key.endswith('price'):
        return f'{float(value):,.0f}'
    return str(value)


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
        self.original_widgets = [(w, w.pack_info()) for w in self.host.pack_slaves()]
        ttk.Style(root).configure('V7609Trade.Treeview', rowheight=31, font=('맑은 고딕', 9))
        ttk.Style(root).map('V7609Trade.Treeview', foreground=[])
        for index, stage in enumerate((HOLD, SOLD)):
            panel = ttk.Frame(self.frame)
            panel.grid(row=0, column=index, sticky='nsew', padx=(0, 5) if index == 0 else (5, 0))
            self.frame.columnconfigure(index, weight=1, uniform='trade', minsize=0)
            bg, fg = palettes[stage]
            title = tk.Label(panel, text='보유종목' if stage == HOLD else '오늘 매도',
                bg=bg, fg=fg, font=('맑은 고딕', 11, 'bold'), pady=7)
            title.grid(row=0, column=0, columnspan=2, sticky='ew')
            columns = ('no', 'name', 'buy_date', 'buy_time', 'buy_price')
            columns += ('current_price',) if stage == HOLD else ('sell_date', 'sell_time', 'sell_price')
            columns += ('net_pnl', 'net_pnl_pct')
            if stage == SOLD:
                columns += ('sell_reason',)
            table = ttk.Treeview(panel, columns=columns, show='headings', height=VISIBLE_ROWS,
                selectmode='browse', style='V7609Trade.Treeview')
            for key in columns:
                label, width = HEADINGS[key]
                table.heading(key, text=label)
                table.column(key, width=width, minwidth=width, stretch=key in ('name', 'sell_reason'),
                    anchor='w' if key in ('name', 'sell_reason') else 'center')
            table.tag_configure('gain', background=bg, foreground=palettes['gain'])
            table.tag_configure('loss', background=bg, foreground=palettes['loss'])
            table.tag_configure('flat', background=bg, foreground=fg)
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
            table.bind('<<TreeviewSelect>>', lambda e, s=stage: self.select(s))
            table.bind('<Return>', lambda e, s=stage: (self.select(s), 'break')[1])
            table.bind('<MouseWheel>', self.wheel)
            table.bind('<Prior>', lambda e, t=table: (t.yview_scroll(-1, 'pages'), 'break')[1])
            table.bind('<Next>', lambda e, t=table: (t.yview_scroll(1, 'pages'), 'break')[1])
            # Prevent the old root navigation handler from moving the hidden table.
            for key, delta in (('<Up>', -1), ('<Down>', 1), ('<Left>', -1), ('<Right>', 1)):
                table.bind(key, lambda e, s=stage, d=delta: self.arrow(s, d))
        self.frame.rowconfigure(0, weight=1)
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
            iid = items[max(0, min(len(items)-1, current + delta))]
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
            for index, row in enumerate(matching, 1):
                values = [str(index) if key == 'no' else display_value(row, key)
                          for key in table['columns']]
                pnl = row.get('net_pnl')
                tag = 'flat' if pnl is None or pnl == 0 else ('gain' if pnl > 0 else 'loss')
                iid = row_key(stage, row, index)
                while iid in retained:
                    iid = f'{iid}#{index}'
                retained.add(iid)
                order.append(iid)
                if table.exists(iid):
                    table.item(iid, values=values, tags=(tag,))
                else:
                    table.insert('', 'end', iid=iid, values=values, tags=(tag,))
                self.row_maps[stage][iid] = row
            for iid in table.get_children():
                if iid not in retained:
                    table.delete(iid)
            for position, iid in enumerate(order):
                table.move(iid, '', position)
            self.labels[stage].configure(
                text=f'총 {len(matching)}건 · 세로 스크롤로 전체 확인')
        if hasattr(self, 'combined_count'):
            counts = {s: sum(r.get('stage') == s for r in rows) for s in (HOLD, SOLD)}
            self.combined_count.configure(textvariable='', text=f'보유 {counts[HOLD]} · 매도 {counts[SOLD]}')

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

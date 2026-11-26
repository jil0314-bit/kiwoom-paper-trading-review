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
MIN_REASON = 60       # V7619: 매도사유 칸 최소 폭(px) → V7634 60(짧은 사유 이름으로 표시)
FIT_PAD = 8           # 글자 폭 + 좌우 여백 (V7634 14→8)
HEADINGS = {'no': ('순번', 44), 'name': ('종목명', 98),
    'buy_date': ('매수일', 76), 'buy_time': ('매수시각', 66), 'buy_price': ('매수가', 67),
    'current_price': ('현재가', 67), 'sell_date': ('매도일', 76),
    'sell_time': ('매도시각', 66), 'sell_price': ('매도가', 67),
    'net_pnl': ('순손익(원)', 90), 'net_pnl_pct': ('순손익률', 72), 'sell_reason': ('매도사유', 152),
    'quantity': ('수량', 56), 'invested': ('매수금액', 92), 'buy_when': ('매수', 92),
    'net_combo': ('순손익(률)', 120), 'sell_combo': ('매도 시각·가격', 110),
    'price_combo': ('평단→현재', 110), 'when_combo': ('매수→매도', 100)}
HOLD_LABELS = {'buy_price': '평단가', 'buy_time': '첫 매수'}   # V7633: 보유표는 평단·첫 매수시각을 보여준다
SOLD_LABELS = {'buy_price': '평단가', 'sell_reason': '사유'}
TRANCHE_TAG = 'tranche'   # V7633: 2·3차까지 산 종목 아래에 차수별 수량·금액 줄
# V7634 (2026-11-26 형 지시): 표 디자인 정리
#  - 화면에 보이는 칸: 보유 = 순번·종목명·매수·평단가·수량·매수금액·현재가·순손익·순손익률
#                      매도 = 순번·종목명·매수·평단가·수량·매수금액·매도시각·매도가·순손익·순손익률·매도사유
#    ('매수' 칸 = 오늘 산 종목은 첫 매수 시각, 이월 종목은 'N일 전 MM/DD')
#  - 종목마다 색 띠(짝/홀 번갈아): 그 종목의 1·2·3차 줄도 같은 띠 → 묶음이 한눈에 보인다(칸 줄긋기 대신)
#  - 이월(1거래일 이상 전에 산) 종목은 두 표 모두 호박색 띠
#  - 본 줄은 굵게, 차수 줄은 연한 글씨
#  - 실제 목록 폭(약 600px)에 글씨 크기 그대로 다 들어가게: 매수금액은 '만' 단위, 순손익·수익률 한 칸,
#    매도 시각·가격 한 칸, 매도사유는 짧은 이름(평단+0.5%·목표+2% 등, 누르면 전체)
HOLD_SHOWN = ('no', 'name', 'buy_when', 'price_combo', 'quantity', 'invested', 'net_combo')
SOLD_SHOWN = ('no', 'name', 'when_combo', 'buy_price', 'invested', 'net_combo', 'sell_reason')
# V7636 (2026-11-26 형 지시 '색을 살짝 더 구분되게 다른 톤으로, 글자는 잘 보이게'):
#   종목마다 번갈아 칠하는 두 색을 '같은 색의 진하기 차이'에서 '서로 다른 색 계열(청록↔남색 등)'로 바꿈.
#   두 색 모두 밝은 글자(흰색 대비 10:1 이상, 빨강·파랑 손익 글자 4:1 이상)가 잘 보이는 어둡지 않은 톤.
BANDS = {   # (짝, 홀) 배경
    HOLD: {'t': ('#123f4a', '#283d63'), 'c': ('#4d3712', '#3d3a1e')},
    '매도종목': {'t': ('#4f1f2b', '#3d2752'), 'c': ('#4d3712', '#3d3a1e')},
}
TRANCHE_FG = '#c3d3e4'
CHIP_TODAY = {HOLD: '#123f4a', '매도종목': '#4f1f2b'}
CHIP_CARRY = '#4d3712'


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


def trading_days_between(earlier, later):
    """두 날짜 사이 거래일 수(주말 제외, 공휴일 구분 안 함). 같은 날=0, 전 거래일=1."""
    if earlier >= later:
        return 0
    days, cursor = 0, earlier
    while cursor < later:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return days


def _as_date(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def buy_age(row, today):
    """V7634: 첫 매수가 오늘 기준 몇 거래일 전인지(매도표는 매도일 기준). 모르면 0."""
    bought = _as_date(row.get('buy_date'))
    ref = _as_date(row.get('sell_date')) if row.get('stage') == '매도종목' else None
    if bought is None:
        return 0
    return trading_days_between(bought, ref or today)


def buy_when_text(row, today):
    """V7634 '매수' 칸: 오늘 산 종목은 'HH:MM', 이월 종목은 'N일전'(날짜·시각은 바로 아래 차수 줄에)."""
    age = buy_age(row, today)
    clock = str(row.get('buy_time') or '')[:5]
    if age <= 0:
        return clock or '-'
    return f"{age}일전"


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
    if key == 'net_combo':   # V7634: '+12,300 (+0.41%)'
        pnl, pct = row.get('net_pnl'), row.get('net_pnl_pct')
        if pnl in (None, '') and pct in (None, ''):
            return '-'
        text = f'{float(pnl):+,.0f}' if pnl not in (None, '') else '-'
        return f'{text}  {float(pct):+.2f}%' if pct not in (None, '') else text
    if key == 'price_combo':  # V7634 보유표: '5,515→5,460'
        avg, cur = row.get('buy_price'), row.get('current_price')
        a = f'{float(avg):,.0f}' if avg not in (None, '') else '-'
        return f'{a}→{float(cur):,.0f}' if cur not in (None, '') else a
    if key == 'sell_combo':  # V7634: '09:29 30,150'
        clock, price = str(row.get('sell_time') or '')[:5], row.get('sell_price')
        return f"{clock or '-'} {float(price):,.0f}" if price not in (None, '') else (clock or '-')
    value = row.get(key)
    if value is None or value == '':
        return '-'
    if key == 'net_pnl_pct':
        return f'{float(value):+.2f}%'
    if key == 'net_pnl':
        return f'{float(value):+,.0f}'
    if key == 'quantity':
        return f'{float(value):,.0f}'
    if key == 'invested':   # V7634: 만 단위(99.9만 · 1,299만)
        return man(value)
    if key.endswith('price'):
        return f'{float(value):,.0f}'
    return str(value)


def man(value):
    """V7634: 금액을 만 단위로 — 1,000만 이상은 정수(1,299만), 미만은 소수 한 자리(99.9만)."""
    amount = float(value or 0) / 1e4
    return f'{amount:,.0f}만' if abs(amount) >= 1000 else f'{amount:,.1f}만'


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


def tranche_value(fill, key, row, today=None):
    """차수 줄에 들어갈 글자. 날짜는 첫 매수와 다를 때만 쓴다."""
    stamp = fill['time']
    day, clock = (stamp[:10], stamp[11:19]) if len(stamp) >= 19 else ('', stamp)
    if key == 'name':
        return f"   └ {fill['tranche']}차"
    if key == 'buy_date':
        return day if day and day != str(row.get('buy_date', '')) else ''
    if key == 'buy_time':
        return clock or '-'
    if key == 'buy_when':   # V7634: 기준일(보유=오늘, 매도=매도일)에 산 차수는 시각만, 그 전이면 'MM/DD HH:MM'
        ref = str(row.get('sell_date') or '')[:10] if row.get('stage') == '매도종목' else str(today or '')
        return (clock[:5] or '-') if (not day or not ref or day == ref) else f"{day[5:7]}/{day[8:10]}"   # 전날 차수는 날짜만
    if key == 'buy_price':
        return f"{fill['price']:,.0f}"
    if key == 'quantity':
        return f"{fill['quantity']:,}"
    if key == 'invested':
        return man(fill['notional'] or fill['price'] * fill['quantity'])
    if key == 'price_combo':
        return f"{fill['price']:,.0f}"
    if key == 'when_combo':
        return tranche_value(fill, 'buy_when', row, today)
    return ''


def reason_label(text):
    """V7634: 매도사유 짧은 이름(표 칸용). 원문은 행을 누르면 아래에 전체 표시."""
    t = str(text)
    if '평단+0.5' in t or '2차후 평단' in t or '추가 뒤 평단' in t:
        return '평단+0.5%'
    if '비용포함+2' in t or '목표+2' in t:
        return '목표+2%'
    for key, label in (('손절', '손절'), ('장마감', '장마감'), ('지시', '형 지시'), ('수동', '수동'), ('시초', '시초가')):
        if key in t:
            return label
    return short_reason(t)


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
            columns = ('no', 'name', 'buy_when', 'buy_date', 'buy_time', 'buy_price', 'quantity', 'invested')
            columns += ('current_price',) if stage == HOLD else ('sell_date', 'sell_time', 'sell_price')
            columns += ('net_pnl', 'net_pnl_pct', 'net_combo', 'price_combo')
            if stage == SOLD:
                columns += ('sell_combo', 'when_combo', 'sell_reason')
            table = ttk.Treeview(panel, columns=columns, show='headings', height=VISIBLE_ROWS,
                selectmode='browse', style=SOLD_STYLE if stage == SOLD else 'V7609Trade.Treeview')
            labels = HOLD_LABELS if stage == HOLD else SOLD_LABELS
            for key in columns:
                label, width = HEADINGS[key]
                table.heading(key, text=labels.get(key, label))
                table.column(key, width=width, minwidth=width, stretch=key in ('name', 'sell_reason'),
                    anchor='w' if key in ('name', 'sell_reason') else 'center')
            # V7634: 글자색(손익)·글꼴은 손익 태그, 배경은 색 띠 태그가 맡는다(서로 겹치지 않게 나눔)
            table.tag_configure('gain', foreground=palettes['gain'], font=('맑은 고딕', 11))
            table.tag_configure('loss', foreground=palettes['loss'], font=('맑은 고딕', 11))
            table.tag_configure('flat', foreground=fg, font=('맑은 고딕', 11))
            table.tag_configure('section', background='#1f4f7a', foreground='#ffffff', font=('맑은 고딕', 10, 'bold'))
            table.tag_configure(TRANCHE_TAG, foreground=TRANCHE_FG, font=('맑은 고딕', 10))
            for kind, pair in BANDS[stage].items():
                for parity, color in zip('ab', pair):
                    table.tag_configure(f'band_{kind}{parity}', background=color)
            # V7634: 제목 줄 오른쪽에 색 띠 안내
            legend = tk.Frame(panel, bg=bg)
            legend.grid(row=0, column=0, columnspan=2, sticky='e', padx=(0, 6))
            for text, color in (('오늘 매수', CHIP_TODAY[stage]), ('이월 매수', CHIP_CARRY)):
                tk.Label(legend, text=f' {text} ', bg=color, fg='#ffffff', font=('맑은 고딕', 9, 'bold'),
                         highlightthickness=1, highlightbackground='#d9e6f2').pack(side='left', padx=2)
            tk.Label(legend, text='└ 차수별 매수', bg=bg, fg=TRANCHE_FG, font=('맑은 고딕', 9)).pack(side='left', padx=(4, 0))
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
            # V7634: 화면에 보일 칸만 고른다(매수일·매수시각·매도일 자료는 그대로 두고 '매수' 칸 하나로 보여 줌)
            table.configure(displaycolumns=HOLD_SHOWN if stage == HOLD else SOLD_SHOWN)
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
            extra = []   # V7634: 표에서 뺀 매도가·수량·매도시각은 누르면 아래에
            if row.get('sell_price') not in (None, ''):
                extra.append(f"매도가 {float(row['sell_price']):,.0f}")
            if row.get('quantity') not in (None, ''):
                extra.append(f"{float(row['quantity']):,.0f}주")
            if row.get('sell_time'):
                extra.append(f"매도 {row['sell_time']}")
            self.labels[stage].configure(text=f"매도사유: {row.get('sell_reason')}" + (' · ' + ' · '.join(extra) if extra else ''))
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
        today = datetime.now(timezone(timedelta(hours=9))).date()
        for stage, table in self.tables.items():
            matching = [row for row in rows if row.get('stage') == stage]
            self.row_maps[stage] = {}
            retained, order = set(), []
            entries = []
            groups = sectioned(matching, today) if stage == HOLD else [(None, matching)]
            for number, group in groups:
                if number is not None:
                    entries.append((None, number, len(group)))
                for row in group:
                    entries.append((row, number, 0))
                    fills = tranche_rows(row)
                    if len(fills) >= 2:   # V7633/V7634: 2·3차까지 샀으면 차수별 줄을 바로 아래에(보유·매도 모두)
                        entries += [(row, number, fill) for fill in fills]
            index = 0
            parent_iid, band = '', 'band_ta'
            for row, number, size in entries:
                if row is None:     # 구역 제목 줄(선택해도 아무 동작 없음)
                    iid = f'section|{number}'
                    values = [f'■ {SECTIONS[number]}' if key == 'name' else (f'{size}종목' if key == 'buy_when' else '')
                              for key in table['columns']]
                    tags = ('section',)
                elif isinstance(size, dict):   # 차수 줄: 고르면 그 종목을 고른 것과 같다
                    iid = f"{parent_iid}|t{size['tranche']}"
                    values = [tranche_value(size, key, row, today) for key in table['columns']]
                    tags = (TRANCHE_TAG, band)
                else:
                    index += 1
                    values = [str(index) if key == 'no' else
                              (buy_when_text(row, today) if key == 'buy_when' else
                               (f"{buy_when_text(row, today)}→{str(row.get('sell_time') or '-')[:5]}" if key == 'when_combo'
                                else display_value(row, key)))
                              for key in table['columns']]
                    pnl = row.get('net_pnl')
                    kind = 'flat' if pnl is None or pnl == 0 else ('gain' if pnl > 0 else 'loss')
                    band = f"band_{'c' if buy_age(row, today) >= 1 else 't'}{'a' if index % 2 else 'b'}"
                    tags = (kind, band)
                    iid = row_key(stage, row, index)
                    while iid in retained:
                        iid = f'{iid}#{index}'
                    parent_iid = iid
                retained.add(iid)
                order.append(iid)
                if table.exists(iid):
                    table.item(iid, values=values, tags=tags)
                else:
                    table.insert('', 'end', iid=iid, values=values, tags=tags)
                if row is not None:
                    self.row_maps[stage][iid] = row
            for iid in table.get_children():
                if iid not in retained:
                    table.delete(iid)
            for position, iid in enumerate(order):
                table.move(iid, '', position)
            carried = sum(buy_age(row, today) >= 1 for row in matching)
            carry_text = f' (이월 {carried})' if carried else ''
            if stage == SOLD:
                pnl_total = sum(float(row.get('net_pnl') or 0) for row in matching)
                text = (f'총 {len(matching)}건{carry_text} · 매수금액 {total_invested(matching):,.0f}원 · '
                        f'순손익 {pnl_total:+,.0f}원 · 행을 누르면 매도사유 전체 표시')
            else:
                text = f'총 {len(matching)}건{carry_text} · 매수금액 합계 {total_invested(matching):,.0f}원 · 세로 스크롤로 전체 확인'
            self.labels[stage].configure(text=text)
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
            if table.exists(iid) and table.tag_has(TRANCHE_TAG, iid):
                texts[iid] = ''     # V7634: 차수 줄에는 매도사유를 쓰지 않는다
                continue
            lines = wrap_text(reason_label(display_value(row, 'sell_reason')), body.measure, room, 1)   # V7634: 1줄
            two_lines = two_lines or len(lines) > 1
            texts[iid] = '\n'.join(lines)
        try:
            base = int(ttk.Style(self.root).lookup('V7609Trade.Treeview', 'rowheight') or 34)
            ttk.Style(self.root).configure(SOLD_STYLE, rowheight=base)   # V7634: 매도표도 1줄 높이(차수 줄이 길어지지 않게)
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

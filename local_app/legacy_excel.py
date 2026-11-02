"""Read legacy XLS values without Office automation or executing workbook code.

Imported only when an XLS attachment is read. The pinned xlrd copy is shipped
with the app, so attachment support never depends on a user's pip environment.
"""
from __future__ import annotations

import math
import struct

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 200_000
MAX_SHARED_STRINGS = 100_000
MAX_SHEETS = 100
MAX_CELL_SLOTS = 200_000
MAX_DATA_ROWS = 20_000
_CELL_RECORDS = frozenset({0x0002, 0x0003, 0x0004, 0x0005, 0x0006,
                           0x0203, 0x0204, 0x0205, 0x0206, 0x0406,
                           0x027E, 0x00BD, 0x00FD, 0x00D6})


class LegacyExcelError(ValueError):
    """Sanitized attachment-facing error, never an upstream parser message."""


class _DiscardLog:
    # xlrd may report document details; do not copy those to logs or diagnostics.
    def write(self, _value):
        pass

    def flush(self):
        pass


def _preflight(raw, xlrd):
    """Bound record/string work before xlrd constructs cell and SST objects."""
    if raw.startswith(xlrd.XLS_SIGNATURE):
        # Reject corrupt allocation parameters before the compound-file reader
        # uses them for division, sector tables or buffer sizes.
        if len(raw) < 512:
            raise LegacyExcelError('XLS 파일이 손상되었어요. Excel에서 새 사본으로 저장해 주세요.')
        version, byte_order, sector_shift, mini_shift = struct.unpack_from('<4H', raw, 26)
        if (byte_order != 0xFFFE or (version, sector_shift) not in {(3, 9), (4, 12)}
                or mini_shift != 6 or len(raw) < 1 << sector_shift):
            raise LegacyExcelError('XLS 파일의 저장 구조를 확인하지 못했어요. XLSX 사본으로 저장해 주세요.')
        container = xlrd.compdoc.CompDoc(raw, logfile=_DiscardLog())
        workbook = container.get_named_stream('Workbook')
        if workbook is None:
            workbook = container.get_named_stream('Book')
        if workbook is None:
            raise LegacyExcelError('XLS 통합 문서를 찾지 못했어요. XLSX 사본으로 저장해 다시 첨부해 주세요.')
    else:
        workbook = raw  # Early BIFF files need not use a compound container.
    if len(workbook) > MAX_BYTES or workbook[:2] not in {b'\x09\x00', b'\x09\x02', b'\x09\x04', b'\x09\x08'}:
        raise LegacyExcelError('XLS 파일의 실제 형식을 확인하지 못했어요. XLSX 사본으로 저장해 다시 첨부해 주세요.')
    offset = count = sheets = sheet_id = cell_slots = 0
    row_widths = {}
    while offset + 4 <= len(workbook):
        record, size = struct.unpack_from('<HH', workbook, offset)
        if not record and not size:
            # Examine padding once. Rechecking a long zero run for every record
            # would turn a small malformed upload into quadratic work.
            if not workbook[offset:].strip(b'\0'):
                break
            raise LegacyExcelError('XLS 파일이 손상되었어요. Excel에서 새 사본으로 저장해 주세요.')
        end = offset + 4 + size
        count += 1
        if end > len(workbook):
            raise LegacyExcelError('XLS 파일이 손상되었어요. Excel에서 새 사본으로 저장해 주세요.')
        if record == 0x002F:  # FILEPASS; do not decrypt or attempt passwords.
            raise LegacyExcelError('암호화된 XLS는 읽지 않아요. 암호를 해제한 사본을 첨부해 주세요.')
        if record == 0x0085:
            sheets += 1
        if record in {0x0009, 0x0209, 0x0409, 0x0809}:
            sheet_id += 1
        if record in _CELL_RECORDS and size >= 4:
            row, col = struct.unpack_from('<HH', workbook, offset + 4)
            if record == 0x00BD and size >= 6:  # MULRK ends with last column.
                col = struct.unpack_from('<H', workbook, end - 2)[0]
            if col > 255:
                raise LegacyExcelError('XLS 파일의 셀 위치를 확인하지 못했어요. XLSX 사본으로 저장해 주세요.')
            key = sheet_id, row
            width = max(row_widths.get(key, 0), col + 1)
            cell_slots += width - row_widths.get(key, 0)
            row_widths[key] = width
        if (count > MAX_RECORDS or sheets > MAX_SHEETS
                or cell_slots > MAX_CELL_SLOTS or len(row_widths) > MAX_DATA_ROWS
                or record == 0x00FC and size >= 8
                and struct.unpack_from('<I', workbook, offset + 8)[0] > MAX_SHARED_STRINGS):
            raise LegacyExcelError('XLS 내부 데이터가 읽기 한도를 넘었어요. 필요한 시트만 나눠 첨부해 주세요.')
        offset = end


def read_values(stream, max_chars):
    """Return bounded text, truncation flag and an honest extraction summary."""
    from ._vendor import xlrd

    raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise LegacyExcelError('XLS는 8 MB까지 읽을 수 있어요. 필요한 시트만 나눠 첨부해 주세요.')
    book = None
    try:
        _preflight(raw, xlrd)
        book = xlrd.open_workbook(file_contents=raw, use_mmap=False, on_demand=True,
                                  ragged_rows=True, formatting_info=False, logfile=_DiscardLog())
        parts, length, rows, partial = [], 0, 0, False

        def add(value):
            nonlocal length, partial
            value += '\n'
            remaining = max_chars - length
            parts.append(value[:max(0, remaining)])
            length += min(len(value), max(0, remaining))
            partial |= len(value) > remaining
            return not partial

        for index in range(book.nsheets):
            if not add(f'[시트: {book.sheet_names()[index]}]'):
                break
            sheet = book.sheet_by_index(index)
            try:
                for row in range(sheet.nrows):
                    cells = []
                    for col in range(sheet.row_len(row)):
                        cell = sheet.cell(row, col)
                        if cell.ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
                            continue
                        value = cell.value
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            try:
                                value = xlrd.xldate_as_datetime(value, book.datemode).isoformat(sep=' ')
                            except (ValueError, OverflowError, xlrd.XLDateError):
                                value = '[날짜 값 확인 필요]'
                        elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                            value = 'TRUE' if value else 'FALSE'
                        elif cell.ctype == xlrd.XL_CELL_ERROR:
                            value = xlrd.error_text_from_code.get(value, '[셀 오류]')
                        elif isinstance(value, float):
                            value = format(value, '.15g') if math.isfinite(value) else '[숫자 값 확인 필요]'
                        cells.append(f'{xlrd.colname(col)}{row + 1}: {value}')
                    if cells:
                        rows += 1
                        if not add(' | '.join(cells)):
                            break
                if partial:
                    break
            finally:
                book.unload_sheet(index)
        return (''.join(parts).rstrip(), partial,
                f'XLS 셀 값 {rows}행 · 저장된 수식 값만 사용 · 매크로·수식·외부 링크 실행 안 함 · 서식·차트·이미지 제외')
    except LegacyExcelError:
        raise
    except (xlrd.XLRDError, xlrd.compdoc.CompDocError, struct.error, IndexError,
            AssertionError, ArithmeticError, ValueError, TypeError, KeyError) as error:
        raise LegacyExcelError('XLS 파일을 읽지 못했어요. 암호·손상을 확인하거나 XLSX 사본을 첨부해 주세요.') from error
    finally:
        if book is not None:
            book.release_resources()

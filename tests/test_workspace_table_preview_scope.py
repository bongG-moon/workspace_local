"""The graph and sort use the bounded logical records returned by the CSV parser."""
import csv
import datetime
import io
import json
from pathlib import Path
import subprocess
import unittest

from local_app.file_preview import _table
from tests.test_workspace_rich_content_frontend import HARNESS, NODE, ROOT


def preview(rows, columns, suffix='.csv'):
    source = io.StringIO(newline='')
    writer = csv.writer(source, delimiter='\t' if suffix == '.tsv' else ',')
    writer.writerow(['날짜', '측정값', '비율'] + [f'설명 {i}' for i in range(columns - 3)])
    for index in range(1, rows + 1):
        day = datetime.date(2026, 1, 1) + datetime.timedelta(days=index - 1)
        writer.writerow([day.isoformat(), 9999 if index == 101 else index, '85%']
                        + ['복수\n행 문자열'] * (columns - 3))
    return _table(Path('표' + suffix), source.getvalue())


class TablePreviewScopeTests(unittest.TestCase):
    def test_count_all_logical_rows_without_including_last_maximum(self):
        result = preview(101, 31, '.tsv')
        self.assertEqual((101, 31), (result['rowCount'], result['columnCount']))
        self.assertEqual((100, 30), (len(result['rows']), len(result['columns'])))
        self.assertTrue(result['rowsTruncated'] and result['columnsTruncated'])
        self.assertEqual(100, max(int(row[1]) for row in result['rows']))
        for rows, columns in [(100, 30), (101, 3), (2, 31)]:
            with self.subTest(rows=rows, columns=columns):
                result = preview(rows, columns)
                self.assertEqual((rows > 100, columns > 30),
                                 (result['rowsTruncated'], result['columnsTruncated']))

    @unittest.skipUnless(NODE, 'Node.js is required for preview rendering checks')
    def test_graph_names_scope_of_extrema_and_source_column_for_each_metric(self):
        case = r"""
        const data=JSON.parse(INPUT);
        WorkspaceResultExplorer.showTable(parent,data,{view:'chart',current:()=>true});
        const scope=cls(parent,'explorer-preview-scope')[0];
        assert.match(scope.textContent,/전체 101행 중 앞 100행/);
        assert.match(scope.textContent,/전체 31열 중 앞 30열/);
        assert.equal(scope.hidden,false);
        assert.match(parent.textContent,/앞 100행의 최솟값 1, 최댓값 100/);
        assert.ok(!parent.textContent.includes('9999'));
        const picker=tag(parent,'select')[0];
        assert.equal(picker.attributes['aria-label'],'그래프 지표 선택');
        assert.equal(tag(picker,'option')[0].textContent,'측정값 (2열)');
        assert.equal(tag(picker,'option')[1].textContent,'비율 (3열)');
        """
        # Use the same lightweight DOM as the shipped rich renderer. The dock
        # is absent here, so this test mounts the production chart directly.
        harness = HARNESS.replace('vm.createContext(context);', """
          context.document.getElementById=()=>null;
          Object.defineProperty(Element.prototype,'classList',{
            get(){return {add:name=>{this.className+=' '+name;}};}
          });
          vm.createContext(context);
        """)
        # Large serialized fixture data travels on stdin, not the Windows argv.
        harness = harness.replace('vm.createContext(context);',
                                  'context.INPUT=' + json.dumps(json.dumps(preview(101, 31)))
                                  + ';vm.createContext(context);')
        result = subprocess.run([NODE, '-', str(ROOT / 'local_app/web/result-explorer.js'), case],
                                input=harness, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

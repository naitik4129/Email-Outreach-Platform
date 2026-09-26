"""Tests the static review tooling only. Never opens a database connection."""
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('migration_checks', ROOT/'scripts/check_migrations.py')
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)


class MigrationChecks(unittest.TestCase):
    def run_check(self, replace=None):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            shutil.copytree(ROOT/'supabase/migrations',root/'supabase/migrations')
            if replace:
                filename,old,new=replace
                path=root/'supabase/migrations'/filename
                source=path.read_text(encoding='utf-8')
                self.assertIn(old,source)
                path.write_text(source.replace(old,new),encoding='utf-8')
            previous=lint.ROOT
            lint.ROOT=root
            output=io.StringIO()
            try:
                with contextlib.redirect_stdout(output): result=lint.check()
            finally: lint.ROOT=previous
            return result,output.getvalue()

    def test_reviewed_chain(self):
        result,output=self.run_check()
        self.assertEqual(result,0,output)
        self.assertIn('63 tables',output)

    def test_original_notification_blocker(self):
        result,output=self.run_check(('0005_operations_platform.sql',
            'CONSTRAINT notifications_workspace_id_key UNIQUE (workspace_id, id),',''))
        self.assertEqual(result,1)
        self.assertIn('missing referenced candidate key notifications',output)

    def test_wrong_parent_candidate_in_later_alter(self):
        result,output=self.run_check(('0003_mailboxes_campaigns.sql',
            'FOREIGN KEY (workspace_id, id, activated_audience_id)\n        REFERENCES public.campaign_audiences (workspace_id, campaign_id, id)',
            'FOREIGN KEY (workspace_id, id, activated_audience_id)\n        REFERENCES public.campaign_audiences (workspace_id, revision, id)'))
        self.assertEqual(result,1)
        self.assertIn('missing referenced candidate key campaign_audiences',output)

    def test_missing_rls(self):
        result,output=self.run_check(('0002_contacts_content.sql',
            'ALTER TABLE public.leads FORCE ROW LEVEL SECURITY;',''))
        self.assertEqual(result,1)
        self.assertIn('missing ENABLE/FORCE RLS leads',output)

    def test_bad_column_grant(self):
        result,output=self.run_check(('0001_initial.sql',
            'GRANT UPDATE (name, defaults)', 'GRANT UPDATE (name, missing_setting)'))
        self.assertEqual(result,1)
        self.assertIn('grant unknown column workspaces.missing_setting',output)

    def test_secret_role_regression(self):
        result,output=self.run_check(('0003_mailboxes_campaigns.sql',
            'GRANT SELECT ON public.mailbox_connections TO app_worker_send;',
            'GRANT SELECT ON public.mailbox_connections TO app_worker_general;'))
        self.assertEqual(result,1)
        self.assertIn('general worker secret access',output)

    def test_quoted_boundaries(self):
        statements=lint.split(lint.tokens("-- ; comment\nSELECT 'a,b;''c', fn(1,2); DO $x$ BEGIN; END; $x$;"),';')
        self.assertEqual(len(statements),2)
        self.assertIn("'a,b;''c'",statements[0])
        self.assertIn('$x$ BEGIN; END; $x$',statements[1])

    def test_unbalanced_parentheses(self):
        with self.assertRaises(ValueError): lint.split(lint.tokens('SELECT fn(1;'),';')


if __name__ == '__main__': unittest.main()

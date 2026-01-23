"""Tests for clerk skill module."""


from clerk.skill import (
    SKILL_CONTENT,
    get_global_skill_path,
    get_local_skill_path,
    get_skill_status,
    install_skill,
    uninstall_skill,
)


class TestSkillPaths:
    def test_global_skill_path(self, tmp_path, monkeypatch):
        """Test global skill path resolution."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)

        path = get_global_skill_path()
        assert path == tmp_path / ".claude" / "skills" / "clerk"

    def test_local_skill_path(self, tmp_path, monkeypatch):
        """Test local skill path resolution."""
        monkeypatch.chdir(tmp_path)

        path = get_local_skill_path()
        assert path == tmp_path / ".claude" / "skills" / "clerk"


class TestInstallSkill:
    def test_install_global(self, tmp_path, monkeypatch):
        """Test installing skill to global location."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)

        result = install_skill(local=False)

        assert result == tmp_path / ".claude" / "skills" / "clerk" / "SKILL.md"
        assert result.exists()
        assert SKILL_CONTENT in result.read_text()

    def test_install_local(self, tmp_path, monkeypatch):
        """Test installing skill to local location."""
        monkeypatch.chdir(tmp_path)

        result = install_skill(local=True)

        assert result == tmp_path / ".claude" / "skills" / "clerk" / "SKILL.md"
        assert result.exists()
        assert SKILL_CONTENT in result.read_text()

    def test_install_creates_parent_dirs(self, tmp_path, monkeypatch):
        """Test that install creates necessary parent directories."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)

        # Ensure .claude doesn't exist
        assert not (tmp_path / ".claude").exists()

        install_skill(local=False)

        assert (tmp_path / ".claude").exists()
        assert (tmp_path / ".claude" / "skills").exists()
        assert (tmp_path / ".claude" / "skills" / "clerk").exists()

    def test_install_overwrites_existing(self, tmp_path, monkeypatch):
        """Test that install overwrites existing SKILL.md."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)

        # Create existing file with different content
        skill_dir = tmp_path / ".claude" / "skills" / "clerk"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("old content")

        install_skill(local=False)

        assert SKILL_CONTENT in skill_file.read_text()


class TestUninstallSkill:
    def test_uninstall_global(self, tmp_path, monkeypatch):
        """Test uninstalling skill from global location."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)

        # First install
        install_skill(local=False)
        skill_dir = tmp_path / ".claude" / "skills" / "clerk"
        assert skill_dir.exists()

        # Then uninstall
        result = uninstall_skill(local=False)

        assert result is True
        assert not skill_dir.exists()

    def test_uninstall_local(self, tmp_path, monkeypatch):
        """Test uninstalling skill from local location."""
        monkeypatch.chdir(tmp_path)

        # First install
        install_skill(local=True)
        skill_dir = tmp_path / ".claude" / "skills" / "clerk"
        assert skill_dir.exists()

        # Then uninstall
        result = uninstall_skill(local=True)

        assert result is True
        assert not skill_dir.exists()

    def test_uninstall_not_installed(self, tmp_path, monkeypatch):
        """Test uninstalling when skill is not installed."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)

        result = uninstall_skill(local=False)

        assert result is False

    def test_uninstall_cleans_empty_parents(self, tmp_path, monkeypatch):
        """Test that uninstall cleans up empty parent directories."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)

        # Install and then uninstall
        install_skill(local=False)
        uninstall_skill(local=False)

        # Skills dir should be cleaned up if empty
        skills_dir = tmp_path / ".claude" / "skills"
        assert not skills_dir.exists()

    def test_uninstall_preserves_sibling_skills(self, tmp_path, monkeypatch):
        """Test that uninstall doesn't remove sibling skills."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)

        # Create another skill
        other_skill = tmp_path / ".claude" / "skills" / "other"
        other_skill.mkdir(parents=True)
        (other_skill / "SKILL.md").write_text("other skill")

        # Install clerk skill
        install_skill(local=False)

        # Uninstall clerk skill
        uninstall_skill(local=False)

        # Other skill should still exist
        assert other_skill.exists()
        assert (tmp_path / ".claude" / "skills").exists()


class TestGetSkillStatus:
    def test_status_none_installed(self, tmp_path, monkeypatch):
        """Test status when no skills are installed."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        status = get_skill_status()

        assert status.global_installed is False
        assert status.global_path is None
        assert status.local_installed is False
        assert status.local_path is None

    def test_status_global_installed(self, tmp_path, monkeypatch):
        """Test status when global skill is installed."""
        # Use different paths for global and local
        global_home = tmp_path / "home"
        local_dir = tmp_path / "project"
        global_home.mkdir()
        local_dir.mkdir()

        monkeypatch.setattr("clerk.skill.Path.home", lambda: global_home)
        monkeypatch.chdir(local_dir)

        install_skill(local=False)
        status = get_skill_status()

        assert status.global_installed is True
        assert status.global_path == global_home / ".claude" / "skills" / "clerk"
        assert status.local_installed is False
        assert status.local_path is None

    def test_status_local_installed(self, tmp_path, monkeypatch):
        """Test status when local skill is installed."""
        # Use different paths for global and local
        global_home = tmp_path / "home"
        local_dir = tmp_path / "project"
        global_home.mkdir()
        local_dir.mkdir()

        monkeypatch.setattr("clerk.skill.Path.home", lambda: global_home)
        monkeypatch.chdir(local_dir)

        install_skill(local=True)
        status = get_skill_status()

        assert status.global_installed is False
        assert status.global_path is None
        assert status.local_installed is True
        assert status.local_path == local_dir / ".claude" / "skills" / "clerk"

    def test_status_both_installed(self, tmp_path, monkeypatch):
        """Test status when both global and local skills are installed."""
        # Use different paths for global and local
        global_home = tmp_path / "home"
        local_dir = tmp_path / "project"
        global_home.mkdir()
        local_dir.mkdir()

        monkeypatch.setattr("clerk.skill.Path.home", lambda: global_home)
        monkeypatch.chdir(local_dir)

        install_skill(local=False)
        install_skill(local=True)
        status = get_skill_status()

        assert status.global_installed is True
        assert status.local_installed is True


class TestSkillContent:
    def test_skill_content_has_frontmatter(self):
        """Test that SKILL.md content has proper frontmatter."""
        assert "---" in SKILL_CONTENT
        assert "name: clerk" in SKILL_CONTENT
        assert "description:" in SKILL_CONTENT

    def test_skill_content_has_documentation(self):
        """Test that SKILL.md content has essential documentation."""
        assert "clerk inbox" in SKILL_CONTENT
        assert "clerk show" in SKILL_CONTENT
        assert "clerk search" in SKILL_CONTENT
        assert "clerk draft" in SKILL_CONTENT
        assert "clerk send" in SKILL_CONTENT

    def test_skill_content_has_safety_info(self):
        """Test that SKILL.md content includes safety information."""
        assert "safety" in SKILL_CONTENT.lower()
        assert "confirmation" in SKILL_CONTENT.lower()

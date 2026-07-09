<?php
/**
 * Small per-domain settings store for the IVR Manager app, used for secrets that
 * must NOT appear in the browsable FusionPBX Default Settings UI (the Google TTS
 * service-account JSON in particular).
 *
 * Backed by its own table, created on demand (the FusionPBX web DB role owns the
 * schema, so CREATE TABLE IF NOT EXISTS is available) — no app-defaults schema
 * step required. Values are write-only from the UI: the app reads them to use,
 * but the settings page never renders a stored secret back.
 */
class ivr_settings {

	private $pdo;
	private $domain_uuid;
	private static $ensured = false;

	public function __construct($pdo, $domain_uuid) {
		$this->pdo = $pdo;
		$this->domain_uuid = $domain_uuid;
		$this->ensure_table();
	}

	private function ensure_table() {
		if (self::$ensured) {
			return;
		}
		$this->pdo->exec(
			"create table if not exists v_ivr_manager_settings ("
			. " domain_uuid text not null,"
			. " setting_name text not null,"
			. " setting_value text,"
			. " primary key (domain_uuid, setting_name))");
		self::$ensured = true;
	}

	public function get($name, $default = null) {
		$stmt = $this->pdo->prepare(
			"select setting_value from v_ivr_manager_settings where domain_uuid = :d and setting_name = :n");
		$stmt->execute(array(':d' => $this->domain_uuid, ':n' => $name));
		$v = $stmt->fetchColumn();
		return ($v === false || $v === null) ? $default : $v;
	}

	public function has($name) {
		$v = $this->get($name, null);
		return $v !== null && $v !== '';
	}

	public function set($name, $value) {
		// portable upsert (no ON CONFLICT, so it works on older PostgreSQL too)
		$this->delete($name);
		$stmt = $this->pdo->prepare(
			"insert into v_ivr_manager_settings (domain_uuid, setting_name, setting_value) values (:d, :n, :v)");
		$stmt->execute(array(':d' => $this->domain_uuid, ':n' => $name, ':v' => $value));
	}

	public function delete($name) {
		$stmt = $this->pdo->prepare(
			"delete from v_ivr_manager_settings where domain_uuid = :d and setting_name = :n");
		$stmt->execute(array(':d' => $this->domain_uuid, ':n' => $name));
	}
}

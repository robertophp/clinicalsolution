-- Citas para terceros: titular (paciente_nombre) vs. beneficiario (nombre_secundario).
-- Ejecutar en el dataset de producción/staging (ej. clinica_datos.citas).
-- Filas existentes: es_para_tercero=false, nombre_secundario=null → paciente_nombre sigue siendo quien asistió.

ALTER TABLE citas ADD COLUMN IF NOT EXISTS es_para_tercero BOOL DEFAULT FALSE;
ALTER TABLE citas ADD COLUMN IF NOT EXISTS nombre_secundario STRING;

-- Edad del beneficiario (niño/niña) cuando es_para_tercero=true.
-- Ejecutar en clinica_datos y clinica_datos_prod según entorno.

ALTER TABLE citas ADD COLUMN IF NOT EXISTS beneficiario_edad INT64;

<script setup>
import { BaseParamField } from '@/lib/base'
import { CALIBRATION_FIELDS, calibrationFieldLabel } from '@/lib/calibrationSettings'

const mzCalibrationParams = defineModel('mzCalibrationParams')
</script>

<template>
  <div class="calibration-fields">
    <BaseParamField
      v-for="field in CALIBRATION_FIELDS"
      :key="field.key"
      :label="calibrationFieldLabel(field)"
      v-model:param="mzCalibrationParams[field.key]"
      :range="field.range"
      hideSlider
      :small="true"
    />
  </div>
</template>

<style scoped>
.calibration-fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  column-gap: 1rem;
  row-gap: 0.3rem;
}

.calibration-fields > * {
  min-width: 0;
  max-width: 100%;
  margin-block: 0 !important;
  padding-block: 0 !important;
}

.calibration-fields :deep(fieldset) {
  width: 100%;
  margin: 0.2rem 0;
  gap: 0.5rem;
  justify-content: flex-start;
}

.calibration-fields :deep(.p-floatlabel) {
  width: 100%;
}

.calibration-fields :deep(.p-field),
.calibration-fields :deep(.field),
.calibration-fields :deep(.form-row),
.calibration-fields :deep(.param-field),
.calibration-fields :deep(.p-formgrid) {
  margin-block: 0 !important;
  padding-block: 0 !important;
}

.calibration-fields :deep(label) {
  margin-bottom: 0rem !important;
}

.calibration-fields :deep(.p-inputnumber),
.calibration-fields :deep(.p-inputnumber-input) {
  width: 100%;
}

@media (max-width: 1050px) {
  .calibration-fields {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 520px) {
  .calibration-fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

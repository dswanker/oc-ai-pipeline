**Study Service API** 

**Overview** 

Study Service API documentation 

**URI scheme** 

*Host* : https://subdomain.build.openclinica.io 

*BasePath* : /study-service 

**Paths** 

**Create Study** 

POST /api/studies 

**Parameters** 

| Type  Body  | Name  studyDTO  *required*  | Description  studyDTO  | Schema  StudyDTO |
| :---- | :---- | :---- | :---- |

**Responses** 

| HTTP  Code  200  | Description  OK  | Schema  StudyDTO |
| :---- | :---- | :---- |
| **201**  | Created  | No Content |
| **401**  | Unauthorized  | No Content |
| **403**  | Forbidden  | No Content |
| **404**  | Not Found  | No Content |

**Consumes** 

• application/json 

**Produces** 

• \*/\* 

1  
**Create Study Environment Site** 

POST /api/study-environments/{studyEnvironmentUuid}/sites 

**Parameters** 

| Type  Path | Name  studyEnviron mentUuid  *required* | Description  studyEnvironmentUuid  | Schema  string |
| :---- | :---- | :---- | :---- |
| **Body** | **studyEnviron mentSiteDTO** *required* | studyEnvironmentSiteDTO  | StudyEnvironmentSi teDTO |

**Responses** 

| HTTP  Code  200  | Description  OK | Schema  StudyEnvironmen tSiteDTO |
| :---- | :---- | :---- |
| **201**  | Created  | No Content |
| **401**  | Unauthorized  | No Content |
| **403**  | Forbidden  | No Content |
| **404**  | Not Found  | No Content |

**Consumes** 

• application/json 

**Produces** 

• \*/\* 

**Get All Studies** 

GET /api/studies 

**Parameters** 

2

| Type  Query | Name  archived  *optional*  | Description  archived  | Schema  boolean  | Default "false" |
| :---- | :---- | :---- | :---- | :---- |
| **Query** | **page**  *optional*  | Page number of the requested page  | integer (int32) |  |
| **Query** | **size**  *optional*  | Size of a page  | integer (int32) |  |
| **Query** | **sort**  *optional* | Sorting criteria in the format: property(,asc|desc). Default sort order is ascending. Multiple sort criteria are supported. | \< string \> array(multi) |  |

**Responses** 

| HTTP  Code  200  | Description  OK | Schema  \< StudyDTO \> array |
| :---- | :---- | :---- |
| **401**  | Unauthorized  | No Content |
| **403**  | Forbidden  | No Content |
| **404**  | Not Found  | No Content |

**Consumes** 

• application/json 

**Produces** 

• \*/\* 

**Get All Study Environments** 

GET /api/studies/{studyUuid}/study-environments 

**Parameters** 

| Type  Path  | Name  studyUuid  *required*  | Description  studyUuid  | Schema  string |
| :---- | :---- | :---- | :---- |

3  
**Responses** 

| HTTP  Code  200  | Description  OK | Schema  \<  StudyEnvironmen tDTO \> array |
| :---- | :---- | :---- |
| **401**  | Unauthorized  | No Content |
| **403**  | Forbidden  | No Content |
| **404**  | Not Found  | No Content |

**Consumes** 

• application/json 

**Produces** 

• \*/\* 

**Definitions** 

**StudyDTO** 

| Name  collectDateOf Birth  *required* | Description  | Schema  enum (ALWAYS, ONLY\_THE\_YEAR, NEVER) |
| :---- | :---- | :---- |
| **collectPersonI d**  *required* |  | enum (ALWAYS, OPTIONAL, NEVER) |
| **collectSex**  *required*  |  | boolean |
| **createdBy**  *optional*  |  | string |
| **createdDate** *optional*  |  | string (date-time) |
| **currentBoard Url**  *optional* |  | string |
| **description**  *required*  |  | string |

4

| Name  | Description  | Schema |
| :---- | :---- | :---- |
| **enforceEnroll mentCap**  *optional* |  | boolean |
| **expectedEndD ate**  *required* |  | string (date) |
| **expectedEnro llment**  *required* |  | integer (int32) |
| **expectedStart Date**  *required* |  | string (date) |
| **lastModifiedB y**  *optional* |  | string |
| **lastModifiedD ate**  *optional* |  | string (date-time) |
| **name**  *required*  |  | string |
| **participantId Template**  *optional* | **Length** : 0 \- 255  | string |
| **phase**  *optional* |  | enum (PHASEI, PHASEII, PHASEIII, PHASEIV,  OTHER\_NON\_IND) |
| **type**  *required* |  | enum  (OBSERVATIONAL, INTERVENTIONAL, OTHER) |
| **uniqueIdentif ier**  *required* | **Length** : 0 \- 30  | string |
| **uuid**  *optional*  |  | string |

**StudyEnvironmentDTO** 

5

| Name  createdBy  *optional*  | Schema  string |
| :---- | :---- |
| **createdDate**  *optional*  | string (date-time) |
| **environmentName**  *optional*  | string |
| **lastModifiedBy**  *optional*  | string |
| **lastModifiedDate**  *optional*  | string (date-time) |
| **latestVersionName**  *optional*  | string |
| **latestVersionPublishedBy**  *optional*  | string |
| **latestVersionPublishedDate**  *optional*  | string (date-time) |
| **oid**  *optional*  | string |
| **published**  *optional*  | boolean |
| **status**  *required* | enum (DESIGN, AVAILABLE, FROZEN, LOCKED, ARCHIVED) |
| **studyName**  *optional*  | string |
| **studyUuid**  *optional*  | string |
| **uuid**  *required*  | string |

**StudyEnvironmentSiteDTO** 

| Name  city  *optional*  | Schema  string |
| :---- | :---- |
| **contactEmail**  *optional*  | string |
| **contactName**  *optional*  | string |

6

| Name  | Schema |
| :---- | :---- |
| **contactPhone**  *optional*  | string |
| **country**  *optional*  | string |
| **createdBy**  *optional*  | string |
| **createdDate**  *optional*  | string (date-time) |
| **expectedEnrollment**  *required*  | integer (int32) |
| **expectedStartDate**  *optional*  | string (date) |
| **irbApprovalDate**  *optional*  | string (date) |
| **lastModifiedBy**  *optional*  | string |
| **lastModifiedDate**  *optional*  | string (date-time) |
| **name**  *required*  | string |
| **oid**  *optional*  | string |
| **principalInvestigator**  *required*  | string |
| **siteUuid**  *optional*  | string |
| **state**  *optional*  | string |
| **status**  *required*  | enum (PENDING, AVAILABLE, FROZEN, LOCKED) |
| **studySiteUuid**  *optional*  | string |
| **timezone**  *required*  | string |
| **uniqueIdentifier**  *required*  | string |
| **uuid**  *optional*  | string |

7

| Name  | Schema |
| :---- | :---- |
| **zip**  *optional*  | string |

**StudySiteDTO** 

| Name  contactEmail  *optional*  | Schema  string |
| :---- | :---- |
| **contactName**  *optional*  | string |
| **contactPhone**  *optional*  | string |
| **createdBy**  *optional*  | string |
| **createdDate**  *optional*  | string (date-time) |
| **expectedEnrollment**  *required*  | integer (int32) |
| **expectedStartDate**  *optional*  | string (date) |
| **id**  *optional*  | integer (int64) |
| **irbApprovalDate**  *optional*  | string (date) |
| **lastModifiedBy**  *optional*  | string |
| **lastModifiedDate**  *optional*  | string (date-time) |
| **principalInvestigator**  *required*  | string |
| **siteId**  *optional*  | integer (int64) |
| **studyId**  *optional*  | integer (int64) |
| **uniqueIdentifier**  *required*  | string |
| **uuid**  *required*  | string |

8  
**SiteDTO** 

| Name  city  *optional*  | Schema  string |
| :---- | :---- |
| **country**  *optional*  | string |
| **createdBy**  *optional*  | string |
| **createdDate**  *optional*  | string (date-time) |
| **id**  *optional*  | integer (int64) |
| **lastModifiedBy**  *optional*  | string |
| **lastModifiedDate**  *optional*  | string (date-time) |
| **name**  *required*  | string |
| **state**  *optional*  | string |
| **uuid**  *required*  | string |
| **zip**  *optional*  | string |

9